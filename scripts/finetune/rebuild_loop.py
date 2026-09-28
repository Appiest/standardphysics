"""The rebuild loop with a trained adapter: propose, snap, judge, and on refusal say why and ask again.

For each chosen held-out room, the model gets the training prompt, answers, and the answer is scored
exactly as the reward scores it (snapped to legal, gate, usefulness, the space type's ADA directives).
A refused answer goes back to the model as its own turn followed by the refusal reason and, when an ADA
directive refused it, that directive's requirement in its own words. Up to `MAX_ATTEMPTS` tries.

    python scripts/finetune/rebuild_loop.py --run runs/finetune/harness --state <training state reference> \\
        --variant webapp-ravida-test-1:whole:as-is --out loop.json
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
from dataclasses import dataclass

from fireworks.training.sdk import FiretitanSamplingParams, FiretitanServiceClient
from multiroom_data import checker_for
from rebuild_judging import refusal_message
from serverless_train import BASE_MODEL, RENDERER, SERVERLESS_URL, TOKENIZER_MODEL, Plan
from standardphysics_agents.training.snapped_prompt import prompt_messages
from standardphysics_agents.training.snapped_reward import score_completion
from standardphysics_agents.training.windows import Window
from standardphysics_contracts import SceneGraph
from training.renderer import get_renderer, get_text_content
from training.utils.tokenizers import load_tokenizer

MAX_ATTEMPTS = 3
TEMPERATURE = 0.7
MAX_TOKENS = 512


def _rows(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@dataclass(frozen=True)
class Answer:
    text: str
    sample_tokens: int
    attempts: int = 1


class Model:
    """A sampler over a saved training state, or over the base model when there is none.

    A sampler snapshot only lives as long as its session, so each Model makes its own.
    """

    def __init__(self, state: str | None):
        self.tokenizer = load_tokenizer(TOKENIZER_MODEL)
        self.renderer = get_renderer(RENDERER, self.tokenizer)
        self.service = FiretitanServiceClient(api_key=os.environ["FIREWORKS_API_KEY"], base_url=SERVERLESS_URL)
        client = (self.service.create_training_client_from_state(state) if state else
                  self.service.create_lora_training_client(base_model=BASE_MODEL, rank=Plan.lora_rank,
                                                           alpha=Plan.lora_alpha))
        snapshot = client.save_weights_for_sampler("rebuild-loop").result().path
        self.sampler = self.service.create_sampling_client(model_path=snapshot, tokenizer=self.tokenizer)
        self.params = FiretitanSamplingParams(max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
                                              stop=self.renderer.get_stop_sequences())

    def prompts(self, conversations: list[list[dict]]) -> list:
        return [self.renderer.build_generation_prompt(messages) for messages in conversations]

    def answer_all(self, prompts: list) -> list[Answer]:
        """One answer per rendered prompt, all in flight at once; a failed request is retried once."""
        futures = [self.sampler.sample(prompt=prompt, num_samples=1, sampling_params=self.params) for prompt in prompts]
        return [self._answer(future, prompt) for future, prompt in zip(futures, prompts)]

    def _answer(self, future, prompt) -> Answer:
        attempts = 1
        try:
            result = future.result(timeout=1800)
        except Exception:
            attempts = 2
            result = self.sampler.sample(prompt=prompt, num_samples=1, sampling_params=self.params).result(timeout=1800)
        tokens = list(list(getattr(result, "sequences", []) or [])[0].tokens or [])
        return Answer(get_text_content(self.renderer.parse_response(tokens)[0]), len(tokens), attempts)

    def answer(self, messages: list[dict]) -> str:
        return self.answer_all(self.prompts([messages]))[0].text

    def close(self) -> None:
        self.sampler.close()
        self.service.close()


def run_loop(model: Model, window: Window, graph: SceneGraph) -> dict:
    checker = checker_for(window)
    messages = prompt_messages(graph, checker)
    attempts = []
    for _ in range(MAX_ATTEMPTS):
        completion = model.answer(messages)
        verdict = score_completion(completion, graph, checker)
        attempts.append({"completion": completion, **verdict.as_dict()})
        if verdict.gate_accepts:
            break
        messages += [{"role": "assistant", "content": completion},
                     {"role": "user", "content": refusal_message(verdict, checker.directives_for(graph))}]
    return {"accepted": attempts[-1]["gate_accepts"], "attempts": attempts}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=pathlib.Path, required=True)
    parser.add_argument("--state", required=True, help="a training state reference, such as rl-state-0012")
    parser.add_argument("--variant", action="append", required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    windows = {row["window_id"]: Window.from_dict(row) for row in _rows(args.run / "windows.jsonl")}
    variants = {row["variant_id"]: row for row in _rows(args.run / "variants.jsonl") if row.get("variant_id")}
    model = Model(args.state)
    try:
        results = {}
        for variant_id in args.variant:
            variant = variants[variant_id]
            results[variant_id] = run_loop(model, windows[variant["window_id"]], SceneGraph.model_validate(variant["graph"]))
            print(variant_id, [(a["reward"], a["reason"]) for a in results[variant_id]["attempts"]], flush=True)
    finally:
        model.close()
    args.out.write_text(json.dumps({"state": args.state, "results": results}, indent=2) + "\n")


if __name__ == "__main__":
    main()
