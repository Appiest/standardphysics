"""SFT then RL for Qwen3.8 27B on Fireworks serverless training, for room 6 or the multi-room set.

One pooled serverless session does everything: a baseline evaluation of the
untrained adapter, LoRA SFT on the search's rearrangements, an evaluation, RL
with our measured checker as the reward (computed here, in this process), a
final evaluation, and optional promotion of both adapters to account models. Nothing is
deployed. Every step records itself in runs/finetune/progress.json, and a rerun
skips what already finished.

Runs inside the cookbook environment (fireworks-ai[training] + fw-ai/cookbook
training package) with this repo's packages on PYTHONPATH:

    python scripts/finetune/serverless_train.py --data runs/finetune/room6/data --run-dir runs/finetune/room6/qwen3p8-27b
    python scripts/finetune/serverless_train.py --dataset multiroom --data runs/finetune/multiroom/v2 \
        --run-dir runs/finetune/multiroom/qwen3p8-27b --progress runs/finetune/multiroom/PROGRESS_MULTIROOM.json \
        --plan-overrides '{"rl_steps": 20}' --max-estimate 42 --measure-tokens

For a new multiroom SFT run, --include-corrections adds the separately
generated train-only correction rows. Use a new run directory and progress file.
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
from menu_data import load as load_menu
from multiroom_results import composition
from multiroom_train_data import load as load_multiroom
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
HARD_STOP_DOLLARS = 72.0
"""A backstop above any single run's budget; the plan's `budget_dollars` is the working cap."""
TRANSIENT_EXIT_CODE = 75
MIN_PLAUSIBLE_PROMPT_TOKENS = 500
"""A room prompt renders to about 2,000 tokens. A handful means the tokenizer
download failed and a stub stood in for it, which would train on garbage."""


ESTIMATE_EXIT_CODE = 4
LOADERS = {"room6": load, "multiroom": load_multiroom, "menu": load_menu}


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
    initial_state: str | None = None


def expected_cost(plan: Plan, data: Room6Data, prompt_tokens: int, sft_tokens: int) -> dict:
    """Uncached prompt, maximum generated length, and full training length for every operation."""
    spend = Spend()
    sft_rows = len(data.sft) * plan.sft_epochs
    spend.train_tokens += sft_rows * sft_tokens
    rollouts = plan.rl_steps * plan.rl_prompts_per_step * plan.rl_group_size
    evals = (3 if plan.rl_steps else 2) * len(data.heldout) * plan.eval_samples
    spend.prefill_tokens += (rollouts + evals) * prompt_tokens
    spend.sample_tokens += (rollouts + evals) * plan.max_sample_tokens
    spend.train_tokens += rollouts * (prompt_tokens + plan.max_sample_tokens)
    return {"sft_rows": sft_rows, "rl_rollouts": rollouts, "eval_samples": evals, **spend.as_dict()}


class Trainer:
    def __init__(self, plan: Plan, data: Room6Data, progress: Progress, run_dir: pathlib.Path, api_key: str):
        self.plan, self.data, self.progress, self.run_dir, self.api_key = plan, data, progress, run_dir, api_key
        self.spend = Spend(**progress.state.get("spend_counters", {}))
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
        limit = min(HARD_STOP_DOLLARS, self.plan.budget_dollars)
        if self.spend.dollars > limit:
            raise BudgetExceeded(f"reserved spend ${self.spend.dollars:.2f} passed ${limit:.2f}")

    # --- sampling and scoring ----------------------------------------------

    def sample(self, snapshot: str, rows: list[dict], count: int, temperature: float) -> list[list]:
        prompts = [self.renderer.build_generation_prompt(row["messages"]) for row in rows]
        self.charge(prefill=sum(prompt.length * count for prompt in prompts),
                    sample=len(prompts) * count * self.plan.max_sample_tokens)
        sampler = self.service.create_sampling_client(model_path=snapshot, tokenizer=self.tokenizer)
        params = FiretitanSamplingParams(max_tokens=self.plan.max_sample_tokens, temperature=temperature,
                                         stop=self.renderer.get_stop_sequences())
        try:
            futures = [sampler.sample(prompt=prompt, num_samples=count, sampling_params=params) for prompt in prompts]
            results = [future.result(timeout=1800) for future in futures]
        finally:
            sampler.close()
        groups = [list(getattr(result, "sequences", []) or []) for result in results]
        self.progress.state["observed_sample_tokens"] = (self.progress.state.get("observed_sample_tokens", 0)
            + sum(len(seq.tokens or []) for group in groups for seq in group))
        self.progress.save()
        return list(zip(prompts, groups, strict=True))

    def text_of(self, sequence) -> str:
        return get_text_content(self.renderer.parse_response(list(sequence.tokens or []))[0])

    def evaluate(self, label: str) -> dict:
        if self.progress.done(f"eval_{label}"):
            return self.progress.get(f"eval_{label}")["summary"]
        self.progress.record(f"eval_{label}", status="running",
                             started_at=self.progress.get(f"eval_{label}").get("started_at", time.time()))
        snapshot = self.client.save_weights_for_sampler(f"ev-{label}"[:17]).result().path
        rows = self.data.heldout
        sampled = self.sample(snapshot, rows, self.plan.eval_samples, self.plan.eval_temperature)
        records, verdicts = [], []
        for row, (_, group) in zip(rows, sampled, strict=True):
            for index, sequence in enumerate(group):
                text = self.text_of(sequence)
                verdict = self.data.score(text, row["variant"])
                verdicts.append(verdict)
                records.append({"variant": row["variant"], "sample": index, "completion": text, **verdict.as_dict()})
        out = self.run_dir / "eval" / f"{label}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(json.dumps(record) + "\n" for record in records))
        summary = summarize(verdicts)
        self.progress.record(f"eval_{label}", status="done", snapshot=snapshot, summary=summary, outputs=str(out),
                             finished_at=time.time())
        print(f"eval {label}: {summary}", flush=True)
        return summary

    # --- supervised ---------------------------------------------------------

    def sft(self) -> None:
        self.progress.record("sft", status="running", started_at=self.progress.get("sft").get("started_at", time.time()))
        datums = [render_messages_to_datum(row["messages"], renderer=self.renderer,
                                           train_on_what="last_assistant_message",
                                           max_seq_len=self.plan.max_seq_len).datum for row in self.data.sft]
        order = list(range(len(datums)))
        step = 0
        completed = self.progress.get("sft").get("completed_batches", 0)
        for epoch in range(self.plan.sft_epochs):
            random.Random(epoch).shuffle(order)
            for start in range(0, len(order), self.plan.sft_batch):
                if step < completed:
                    step += 1
                    continue
                batch = [datums[i] for i in order[start:start + self.plan.sft_batch]]
                self.charge(train=sum(datum.model_input.length for datum in batch))
                self.client.forward_backward(batch, "cross_entropy").result()
                self.client.optim_step(tinker.AdamParams(learning_rate=self.plan.sft_learning_rate,
                                                         beta1=0.9, beta2=0.95, eps=1e-8)).result()
                step += 1
                name = f"sft-state-{step:04d}"
                self.client.save_state(name).result(timeout=900)
                self.progress.record("sft", status="running", epoch=epoch, completed_batches=step,
                                     state_ref=self.state_reference(name))
        self.client.save_state("sft-state").result(timeout=900)
        self.client.save_weights_for_sampler("sft-final").result()
        self.progress.record("sft", status="done", optimizer_steps=step, rows=len(datums),
                             state_ref=self.state_reference("sft-state"), finished_at=time.time(), **self.session)

    # --- reinforcement ------------------------------------------------------

    def rl(self, first_step: int) -> None:
        self.progress.record("rl", status="running", started_at=self.progress.get("rl").get("started_at", time.time()))
        rows = self.data.rl
        for step in range(first_step, self.plan.rl_steps):
            picked = rl_rows_for_step(rows, step, self.plan.rl_prompts_per_step)
            snapshot = self.client.save_weights_for_sampler(f"rl-{step:04d}").result().path
            sampled = self.sample(snapshot, picked, self.plan.rl_group_size, self.plan.rl_temperature)
            datums, rewards = self.rl_datums(picked, sampled)
            if datums:
                self.charge(train=sum(datum.model_input.length for datum in datums))
                self.client.forward_backward(datums, "importance_sampling").result()
                self.client.optim_step(tinker.AdamParams(learning_rate=self.plan.rl_learning_rate,
                                                         beta1=0.9, beta2=0.95, eps=1e-12)).result()
            self.after_rl_step(step, rewards, len(datums))
        self.progress.record("rl", status="done", completed_steps=self.plan.rl_steps,
                             finished_at=time.time(), **self.session)

    def after_rl_step(self, step: int, rewards: list[float], trained: int) -> None:
        mean = sum(rewards) / len(rewards) if rewards else 0.0
        line = {"step": step, "mean_reward": round(mean, 4), "samples": len(rewards), "trained_datums": trained,
                "accepted": sum(1 for r in rewards if r > 0), "spend": self.spend.as_dict()}
        with (self.run_dir / "rl_metrics.jsonl").open("a") as handle:
            handle.write(json.dumps(line) + "\n")
        print(f"rl {line}", flush=True)
        fields = {"status": "running", "completed_steps": step + 1}
        name = f"rl-state-{step + 1:04d}"
        self.client.save_state(name).result(timeout=900)
        fields["state_ref"] = self.state_reference(name)
        self.progress.record("rl", **fields, **self.session)

    def rl_datums(self, rows: list[dict], sampled: list) -> tuple[list, list[float]]:
        datums, all_rewards = [], []
        for row, (prompt, group) in zip(rows, sampled, strict=True):
            usable = [seq for seq in group if seq.tokens and seq.logprobs and len(seq.logprobs) == len(seq.tokens)]
            rewards = [self.data.score(self.text_of(seq), row["variant"]).reward for seq in usable]
            all_rewards.extend(rewards)
            if len(set(rewards)) > 1:
                datums.extend(self.group_datums(prompt, usable, advantages(rewards)))
        return datums, all_rewards

    def group_datums(self, prompt, sequences, group_advantages) -> list:
        start = prompt.length - 1
        made = []
        for sequence, advantage in zip(sequences, group_advantages, strict=True):
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
            model_id = f"{output_model_id}-{self.session['run_id']}"
            model = control.promote_session_checkpoint(name=target["name"], output_model_id=model_id,
                                                       base_model=BASE_MODEL)
        finally:
            control.close()
        return model.get("name") if isinstance(model, dict) else str(model)


def advantages(rewards: list[float]) -> list[float]:
    mean = sum(rewards) / len(rewards)
    spread = math.sqrt(sum((r - mean) ** 2 for r in rewards) / max(1, len(rewards) - 1)) or 1.0
    return [(r - mean) / spread for r in rewards]


def rl_rows_for_step(rows: list[dict], step: int, prompts_per_step: int) -> list[dict]:
    by_room: dict[str, list[dict]] = {}
    for row in rows:
        by_room.setdefault(row["window"], []).append(row)
    rooms = sorted(by_room)
    if not rooms:
        raise ValueError("RL needs at least one training room")
    picked = []
    for index in range(min(prompts_per_step, len(rows))):
        room = rooms[(step * prompts_per_step + index) % len(rooms)]
        variants = by_room[room]
        picked.append(variants[(step * prompts_per_step + index) // len(rooms) % len(variants)])
    return picked


def promote_optional(trainer: Trainer, step: str, checkpoint_prefix: str, output_model_id: str) -> None:
    progress = trainer.progress
    if progress.get(step).get("status") in ("done", "failed"):
        return
    progress.record(step, status="running", started_at=progress.get(step).get("started_at", time.time()))
    try:
        model = trainer.promote(checkpoint_prefix, output_model_id)
    except Exception as error:
        progress.record(step, status="failed", error=str(error), finished_at=time.time())
        print(f"optional {step} failed: {error}", flush=True)
        return
    progress.record(step, status="done", model=model, finished_at=time.time())


def run_sft_phase(trainer: Trainer) -> None:
    progress = trainer.progress
    if progress.done("promote_sft"):
        return
    if progress.done("sft"):
        trainer.connect(progress.get("sft")["state_ref"])
        trainer.client.save_weights_for_sampler("sft-final").result()
    else:
        checkpoint = progress.get("sft").get("state_ref")
        trainer.connect(checkpoint or trainer.plan.initial_state, with_optimizer=bool(checkpoint))
        trainer.evaluate("base")
        trainer.sft()
    trainer.evaluate("sft")
    promote_optional(trainer, "promote_sft", "sft-final", trainer.plan.sft_model_id)


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
    promote_optional(trainer, "promote_rl", "rl-final", trainer.plan.rl_model_id)


def measured_tokens(data, plan: Plan) -> tuple[int, int]:
    """Maximum actual prompt and supervised datum lengths; generation uses max_sample_tokens."""
    tokenizer = load_tokenizer(TOKENIZER_MODEL)
    renderer = get_renderer(RENDERER, tokenizer)
    prompts = [renderer.build_generation_prompt(row["messages"]).length for row in [*data.rl, *data.heldout]]
    sft = [render_messages_to_datum(row["messages"], renderer=renderer, train_on_what="last_assistant_message",
                                    max_seq_len=plan.max_seq_len).datum.model_input.length for row in data.sft]
    if not prompts or not sft:
        raise ValueError("training requires supervised examples and evaluation prompts")
    if min(prompts) < MIN_PLAUSIBLE_PROMPT_TOKENS:
        raise ValueError("tokenizer produced implausibly short prompts")
    return max(prompts), max(sft)


def transient_error(error: Exception) -> bool:
    code = (getattr(error, "status_code", None) or getattr(error, "code", None)
            or getattr(getattr(error, "response", None), "status_code", None))
    return isinstance(error, (ConnectionError, TimeoutError)) or code in (408, 429, 500, 502, 503, 504)


def validate_correction_mode(progress: Progress, include_corrections: bool) -> None:
    prior = progress.state.get("plan", {}).get("include_corrections")
    if prior is not None and prior != include_corrections:
        raise ValueError("correction mode differs from this training run; use a fresh progress file")
    if prior is None and include_corrections and progress.get("sft"):
        raise ValueError("existing SFT run has unknown correction mode; use a fresh progress file")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", choices=sorted(LOADERS), default="room6")
    parser.add_argument("--include-corrections", action="store_true",
                        help="add generated multiroom correction rows to SFT; use a fresh run directory")
    parser.add_argument("--data", type=pathlib.Path, required=True)
    parser.add_argument("--run-dir", type=pathlib.Path, required=True)
    parser.add_argument("--progress", type=pathlib.Path, default=pathlib.Path("runs/finetune/progress.json"))
    parser.add_argument("--plan-overrides", type=json.loads, default={})
    parser.add_argument("--max-estimate", type=float, default=None,
                        help="refuse to launch when the pessimistic estimate is above this many dollars")
    parser.add_argument("--measure-tokens", action="store_true")
    parser.add_argument("--estimate-only", action="store_true")
    parser.add_argument("--prompt-tokens", type=int, default=2300)
    parser.add_argument("--answer-tokens", type=int, default=200)
    parser.add_argument("--phases", choices=("sft", "sft,rl"), default="sft,rl",
                        help="`sft` stops after supervised training, its evaluation and optional promotion")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.include_corrections and args.dataset != "multiroom":
        raise ValueError("correction rows are only available for the multiroom dataset")
    loader = LOADERS[args.dataset]
    data = loader(args.data, include_corrections=True) if args.include_corrections else loader(args.data)
    plan, progress = Plan(**args.plan_overrides), Progress(args.progress)
    validate_correction_mode(progress, args.include_corrections)
    prompt_tokens, sft_tokens = (measured_tokens(data, plan) if args.measure_tokens
                                    else (args.prompt_tokens, args.answer_tokens))
    estimate = {**expected_cost(plan, data, prompt_tokens, sft_tokens),
                "prompt_tokens_each": prompt_tokens, "sft_tokens_each": sft_tokens,
                "maximum_sample_tokens_each": plan.max_sample_tokens}
    if args.dataset == "multiroom":
        progress.set("data_composition", composition(args.data))
    progress.set("plan", {**asdict(plan), "base_model": BASE_MODEL, "renderer": RENDERER,
                          "include_corrections": args.include_corrections,
                          "expected_cost": estimate, "max_estimate": args.max_estimate})
    print(json.dumps(estimate), flush=True)
    if args.max_estimate is not None and estimate["estimated_dollars"] > args.max_estimate:
        print(f"estimate ${estimate['estimated_dollars']:.2f} is above ${args.max_estimate:.2f}; not launching")
        raise SystemExit(ESTIMATE_EXIT_CODE)
    if args.estimate_only:
        return
    args.run_dir.mkdir(parents=True, exist_ok=True)
    trainer = Trainer(plan, data, progress, args.run_dir, os.environ["FIREWORKS_API_KEY"])
    try:
        run_sft_phase(trainer)
        if "rl" in args.phases.split(",") and plan.rl_steps:
            run_rl_phase(trainer)
    except BudgetExceeded as stop:
        progress.record("budget", status="stopped", reason=str(stop))
        raise SystemExit(BUDGET_EXIT_CODE) from stop
    except Exception as error:
        if not transient_error(error):
            raise
        progress.record("network", status="retry", reason=type(error).__name__)
        raise SystemExit(TRANSIENT_EXIT_CODE) from error
    finally:
        trainer.close()


if __name__ == "__main__":
    main()
