"""SFT then RL for Qwen3.8 27B on room 6, on Fireworks serverless training.

One pooled serverless session does everything: a baseline evaluation of the
untrained adapter, LoRA SFT on the search's rearrangements, an evaluation, RL
with our measured checker as the reward (computed here, in this process), a
final evaluation, and promotion of both adapters to account models. Nothing is
deployed. Every step records itself in PROGRESS_FINETUNE.json, and a rerun
skips what already finished.

Runs inside the cookbook environment (fireworks-ai[training] + fw-ai/cookbook
training package) with this repo's packages on PYTHONPATH:

    python scripts/finetune/serverless_train.py --data runs/finetune/room6/data --run-dir runs/finetune/room6/qwen3p8-27b
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import random
import time
from dataclasses import asdict, dataclass

import tinker
from fireworks.training.sdk import FiretitanSamplingParams, FiretitanServiceClient, FireworksClient
from progress import Progress, Spend
from room6_data import Room6Data, load
from standardphysics_agents.training.reward import summarize
from training.renderer import get_renderer, get_text_content
from training.utils.supervised import render_messages_to_datum
from training.utils.tokenizers import load_tokenizer

BASE_MODEL = "accounts/fireworks/models/qwen3p8-27b"
TOKENIZER_MODEL = "Qwen/Qwen3.8-27B"
RENDERER = "qwen3_8_disable_thinking_interleaved"
SERVERLESS_URL = "https://api.fireworks.ai/training/v1/serverless"
CONTROL_URL = "https://api.fireworks.ai"
BUDGET_EXIT_CODE = 3
MIN_PLAUSIBLE_PROMPT_TOKENS = 500
"""A room prompt renders to about 2,000 tokens. A handful means the tokenizer
download failed and a stub stood in for it, which would train on garbage."""


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Plan:
    lora_rank: int = 32
    lora_alpha: int = 64
    max_seq_len: int = 8192
    sft_epochs: int = 3
    sft_batch: int = 8
    sft_learning_rate: float = 1e-4
    rl_steps: int = 24
    rl_prompts_per_step: int = 6
    rl_group_size: int = 8
    rl_learning_rate: float = 2e-5
    rl_temperature: float = 1.0
    rl_state_every: int = 6
    eval_samples: int = 4
    eval_temperature: float = 0.7
    max_sample_tokens: int = 512
    budget_dollars: float = 25.0
    sft_model_id: str = "room6-qwen3p8-27b-sft"
    rl_model_id: str = "room6-qwen3p8-27b-rl"


def expected_cost(plan: Plan, data: Room6Data, prompt_tokens: int, answer_tokens: int) -> dict:
    """What the plan should cost, before anything is launched, at the serverless rates."""
    spend = Spend()
    sft_rows = len(data.sft) * plan.sft_epochs
    spend.train_tokens += sft_rows * (prompt_tokens + answer_tokens)
    rollouts = plan.rl_steps * plan.rl_prompts_per_step * plan.rl_group_size
    evals = 3 * len(data.heldout) * plan.eval_samples
    spend.prefill_tokens += (rollouts + evals) * prompt_tokens
    spend.sample_tokens += (rollouts + evals) * answer_tokens
    spend.train_tokens += rollouts * (prompt_tokens + answer_tokens)
    return {"sft_rows": sft_rows, "rl_rollouts": rollouts, "eval_samples": evals, **spend.as_dict()}


class Trainer:
    def __init__(self, plan: Plan, data: Room6Data, progress: Progress, run_dir: pathlib.Path, api_key: str):
        self.plan, self.data, self.progress, self.run_dir, self.api_key = plan, data, progress, run_dir, api_key
        self.spend = Spend(**{k: v for k, v in progress.state.get("spend_counters", {}).items()})
        self.tokenizer = load_tokenizer(TOKENIZER_MODEL)
        self.renderer = get_renderer(RENDERER, self.tokenizer)
        probe = self.renderer.build_generation_prompt(data.heldout[0]["messages"])
        if probe.length < MIN_PLAUSIBLE_PROMPT_TOKENS:
            raise RuntimeError(f"tokenizer {TOKENIZER_MODEL} did not load properly: prompt is {probe.length} tokens")
        self.service = FiretitanServiceClient(api_key=api_key, base_url=SERVERLESS_URL)
        self.client = None
        self.session: dict = {}

    # --- session ------------------------------------------------------------

    def connect(self, from_state: str | None = None, with_optimizer: bool = False) -> None:
        if from_state and with_optimizer:
            self.client = self.service.create_training_client_from_state_with_optimizer(from_state)
        elif from_state:
            self.client = self.service.create_training_client_from_state(from_state)
        else:
            self.client = self.service.create_lora_training_client(
                base_model=BASE_MODEL, rank=self.plan.lora_rank, alpha=self.plan.lora_alpha)
        self.session = {"session": getattr(self.service, "training_session_name", None)
                        or getattr(self.service, "training_session_id", None),
                        "run_id": getattr(self.client, "run_id", None)}
        self.progress.state["jobs"].append({**self.session, "kind": "serverless training session",
                                            "base_model": BASE_MODEL, "from_state": from_state,
                                            "opened_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        self.progress.save()
        print(f"session {self.session}", flush=True)

    def state_reference(self, name: str) -> str:
        account = str(self.session["session"]).split("/")[1] if "/" in str(self.session["session"]) else None
        if account is None:
            control = FireworksClient(api_key=self.api_key, base_url=CONTROL_URL)
            account = control.account_id
            control.close()
        return f"{account}/{self.session['run_id']}/{name}"

    def close(self) -> None:
        self.service.close()

    # --- metering -----------------------------------------------------------

    def charge(self, prefill: int = 0, sample: int = 0, train: int = 0) -> None:
        self.spend.prefill_tokens += prefill
        self.spend.sample_tokens += sample
        self.spend.train_tokens += train
        self.progress.state["spend_counters"] = {
            "prefill_tokens": self.spend.prefill_tokens, "sample_tokens": self.spend.sample_tokens,
            "train_tokens": self.spend.train_tokens}
        self.progress.set("spend", self.spend.as_dict())
        if self.spend.dollars > self.plan.budget_dollars:
            raise BudgetExceeded(f"estimated spend ${self.spend.dollars:.2f} passed ${self.plan.budget_dollars:.2f}")

    # --- sampling and scoring ----------------------------------------------

    def sample(self, snapshot: str, rows: list[dict], count: int, temperature: float) -> list[list]:
        prompts = [self.renderer.build_generation_prompt(row["messages"]) for row in rows]
        sampler = self.service.create_sampling_client(model_path=snapshot, tokenizer=self.tokenizer)
        params = FiretitanSamplingParams(max_tokens=self.plan.max_sample_tokens, temperature=temperature,
                                         stop=self.renderer.get_stop_sequences())
        try:
            futures = [sampler.sample(prompt=prompt, num_samples=count, sampling_params=params) for prompt in prompts]
            results = [future.result(timeout=1800) for future in futures]
        finally:
            sampler.close()
        groups = [list(getattr(result, "sequences", []) or []) for result in results]
        self.charge(prefill=sum(p.length * count for p in prompts),
                    sample=sum(len(seq.tokens or []) for group in groups for seq in group))
        return list(zip(prompts, groups))

    def text_of(self, sequence) -> str:
        return get_text_content(self.renderer.parse_response(list(sequence.tokens or []))[0])

    def evaluate(self, label: str) -> dict:
        if self.progress.done(f"eval_{label}"):
            return self.progress.get(f"eval_{label}")["summary"]
        snapshot = self.client.save_weights_for_sampler(f"ev-{label}"[:17]).result().path
        rows = self.data.heldout
        sampled = self.sample(snapshot, rows, self.plan.eval_samples, self.plan.eval_temperature)
        records, verdicts = [], []
        for row, (_, group) in zip(rows, sampled):
            for index, sequence in enumerate(group):
                text = self.text_of(sequence)
                verdict = self.data.score(text, row["variant"])
                verdicts.append(verdict)
                records.append({"variant": row["variant"], "sample": index, "completion": text, **verdict.as_dict()})
        out = self.run_dir / "eval" / f"{label}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(json.dumps(record) + "\n" for record in records))
        summary = summarize(verdicts)
        self.progress.record(f"eval_{label}", status="done", snapshot=snapshot, summary=summary, outputs=str(out))
        print(f"eval {label}: {summary}", flush=True)
        return summary

    # --- supervised ---------------------------------------------------------

    def sft(self) -> None:
        datums = [render_messages_to_datum(row["messages"], renderer=self.renderer,
                                           train_on_what="last_assistant_message",
                                           max_seq_len=self.plan.max_seq_len).datum for row in self.data.sft]
        order = list(range(len(datums)))
        step = 0
        for epoch in range(self.plan.sft_epochs):
            random.Random(epoch).shuffle(order)
            for start in range(0, len(order), self.plan.sft_batch):
                batch = [datums[i] for i in order[start:start + self.plan.sft_batch]]
                self.client.forward_backward(batch, "cross_entropy").result()
                self.client.optim_step(tinker.AdamParams(learning_rate=self.plan.sft_learning_rate,
                                                         beta1=0.9, beta2=0.95, eps=1e-8)).result()
                self.charge(train=sum(datum.model_input.length for datum in batch))
                step += 1
                self.progress.record("sft", status="running", epoch=epoch, optimizer_steps=step)
        self.client.save_state("sft-state").result(timeout=900)
        self.client.save_weights_for_sampler("sft-final").result()
        self.progress.record("sft", status="done", optimizer_steps=step, rows=len(datums),
                             state_ref=self.state_reference("sft-state"), **self.session)

    # --- reinforcement ------------------------------------------------------

    def rl(self, first_step: int) -> None:
        rows = self.data.rl
        for step in range(first_step, self.plan.rl_steps):
            picked = random.Random(1000 + step).sample(rows, min(self.plan.rl_prompts_per_step, len(rows)))
            snapshot = self.client.save_weights_for_sampler(f"rl-{step:04d}").result().path
            sampled = self.sample(snapshot, picked, self.plan.rl_group_size, self.plan.rl_temperature)
            datums, rewards = self.rl_datums(picked, sampled)
            if datums:
                self.client.forward_backward(datums, "importance_sampling").result()
                self.client.optim_step(tinker.AdamParams(learning_rate=self.plan.rl_learning_rate,
                                                         beta1=0.9, beta2=0.95, eps=1e-12)).result()
                self.charge(train=sum(datum.model_input.length for datum in datums))
            self.after_rl_step(step, rewards, len(datums))
        self.progress.record("rl", status="done", completed_steps=self.plan.rl_steps, **self.session)

    def after_rl_step(self, step: int, rewards: list[float], trained: int) -> None:
        mean = sum(rewards) / len(rewards) if rewards else 0.0
        line = {"step": step, "mean_reward": round(mean, 4), "samples": len(rewards), "trained_datums": trained,
                "accepted": sum(1 for r in rewards if r > 0), "spend": self.spend.as_dict()}
        with (self.run_dir / "rl_metrics.jsonl").open("a") as handle:
            handle.write(json.dumps(line) + "\n")
        print(f"rl {line}", flush=True)
        fields = {"status": "running", "completed_steps": step + 1}
        if (step + 1) % self.plan.rl_state_every == 0:
            name = f"rl-state-{step + 1:04d}"
            self.client.save_state(name).result(timeout=900)
            fields["state_ref"] = self.state_reference(name)
        self.progress.record("rl", **fields, **self.session)

    def rl_datums(self, rows: list[dict], sampled: list) -> tuple[list, list[float]]:
        datums, all_rewards = [], []
        for row, (prompt, group) in zip(rows, sampled):
            usable = [seq for seq in group if seq.tokens and seq.logprobs and len(seq.logprobs) == len(seq.tokens)]
            rewards = [self.data.score(self.text_of(seq), row["variant"]).reward for seq in usable]
            all_rewards.extend(rewards)
            if len(set(rewards)) > 1:
                datums.extend(self.group_datums(prompt, usable, advantages(rewards)))
        return datums, all_rewards

    def group_datums(self, prompt, sequences, group_advantages) -> list:
        start = prompt.length - 1
        made = []
        for sequence, advantage in zip(sequences, group_advantages):
            tokens = list(sequence.tokens)
            model_input = prompt.append(tinker.EncodedTextChunk(tokens=tokens[:-1]))
            made.append(tinker.Datum(model_input=model_input, loss_fn_inputs={
                "target_tokens": [0] * start + tokens,
                "logprobs": [0.0] * start + [float(x) for x in sequence.logprobs],
                "advantages": [0.0] * start + [advantage] * (model_input.length - start),
            }))
        return made

    # --- promotion ------------------------------------------------------------

    def promote(self, checkpoint_prefix: str, output_model_id: str) -> str:
        control = FireworksClient(api_key=self.api_key, base_url=CONTROL_URL)
        try:
            session = self.service.training_session_name or self.session["session"]
            rows = control.list_training_session_checkpoints(session)
            label = f"{self.session['run_id']}-{checkpoint_prefix}"
            target = next(row for row in rows if row.get("promotable")
                          and str(row.get("name", "")).rsplit("/", 1)[-1].startswith(label))
            model = control.promote_session_checkpoint(name=target["name"], output_model_id=output_model_id,
                                                       base_model=BASE_MODEL)
        finally:
            control.close()
        return model.get("name") if isinstance(model, dict) else str(model)


def advantages(rewards: list[float]) -> list[float]:
    mean = sum(rewards) / len(rewards)
    spread = math.sqrt(sum((r - mean) ** 2 for r in rewards) / max(1, len(rewards) - 1)) or 1.0
    return [(r - mean) / spread for r in rewards]


def run_sft_phase(trainer: Trainer) -> None:
    progress = trainer.progress
    if progress.done("promote_sft"):
        return
    if progress.done("sft"):
        trainer.connect(progress.get("sft")["state_ref"])
        trainer.client.save_weights_for_sampler("sft-final").result()
    else:
        trainer.connect()
        trainer.evaluate("base")
        trainer.sft()
    trainer.evaluate("sft")
    model = trainer.promote("sft-final", trainer.plan.sft_model_id)
    progress.record("promote_sft", status="done", model=model)


def run_rl_phase(trainer: Trainer) -> None:
    progress = trainer.progress
    if progress.done("rl") and progress.done("promote_rl"):
        return
    rl = progress.get("rl")
    if trainer.client is None or rl.get("state_ref"):
        resume = rl.get("state_ref")
        trainer.connect(resume or progress.get("sft")["state_ref"], with_optimizer=bool(resume))
    if not progress.done("rl"):
        trainer.rl(first_step=rl.get("completed_steps", 0) if rl.get("state_ref") else 0)
    trainer.client.save_weights_for_sampler("rl-final").result()
    trainer.evaluate("rl")
    model = trainer.promote("rl-final", trainer.plan.rl_model_id)
    progress.record("promote_rl", status="done", model=model)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=pathlib.Path, required=True)
    parser.add_argument("--run-dir", type=pathlib.Path, required=True)
    parser.add_argument("--progress", type=pathlib.Path, default=pathlib.Path("PROGRESS_FINETUNE.json"))
    parser.add_argument("--estimate-only", action="store_true")
    parser.add_argument("--prompt-tokens", type=int, default=2300)
    parser.add_argument("--answer-tokens", type=int, default=200)
    args = parser.parse_args()
    plan, data, progress = Plan(), load(args.data), Progress(args.progress)
    estimate = expected_cost(plan, data, args.prompt_tokens, args.answer_tokens)
    progress.set("plan", {**asdict(plan), "base_model": BASE_MODEL, "renderer": RENDERER,
                          "expected_cost": estimate})
    print(json.dumps(estimate), flush=True)
    if args.estimate_only:
        return
    args.run_dir.mkdir(parents=True, exist_ok=True)
    trainer = Trainer(plan, data, progress, args.run_dir, os.environ["FIREWORKS_API_KEY"])
    try:
        run_sft_phase(trainer)
        run_rl_phase(trainer)
    except BudgetExceeded as stop:
        progress.record("budget", status="stopped", reason=str(stop))
        raise SystemExit(BUDGET_EXIT_CODE) from stop
    finally:
        trainer.close()


if __name__ == "__main__":
    main()
