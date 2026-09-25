"""The rebuild loop with a trained adapter: propose, snap, judge, and on refusal say why and ask again.

For each chosen held-out room, the model gets the training prompt, answers, and the answer is scored
exactly as the reward scores it (snapped to legal, gate, usefulness, the space type's ADA directives).
A refused answer goes back to the model as its own turn followed by the refusal reason and, when an ADA
directive refused it, that directive's requirement in its own words. Up to `MAX_ATTEMPTS` tries.

    python scripts/finetune/rebuild_loop.py --run runs/finetune/harness --snapshot <sampler path> \\
        --variant webapp-ravida-test-1:whole:as-is --out loop.json
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib

from fireworks.training.sdk import FiretitanSamplingParams, FiretitanServiceClient
from multiroom_data import checker_for
from standardphysics_agents.training import prompt_messages, score_completion
from standardphysics_agents.training.windows import Window
from standardphysics_contracts import SceneGraph
from training.renderer import get_renderer, get_text_content
from training.utils.tokenizers import load_tokenizer

SERVERLESS_URL = "https://api.fireworks.ai/training/v1/serverless"
TOKENIZER_MODEL = "Qwen/Qwen3.8-27B"
RENDERER = "qwen3_8_disable_thinking_interleaved"
MAX_ATTEMPTS = 3
TEMPERATURE = 0.7
MAX_TOKENS = 512


def _rows(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def refusal_message(verdict, directives) -> str:
    lines = [f"That layout was refused: {verdict.reason}."]
    if verdict.reason.startswith("precedent_violation"):
        lines += [f"ADA requirement ({directive.title}): {directive.plain_english_warning}" for directive in directives]
    lines.append("Propose a different layout that clears the problems without that. Answer with JSON only.")
    return " ".join(lines)


class Model:
    def __init__(self, snapshot: str):
        self.tokenizer = load_tokenizer(TOKENIZER_MODEL)
        self.renderer = get_renderer(RENDERER, self.tokenizer)
        self.service = FiretitanServiceClient(api_key=os.environ["FIREWORKS_API_KEY"], base_url=SERVERLESS_URL)
        self.sampler = self.service.create_sampling_client(model_path=snapshot, tokenizer=self.tokenizer)
        self.params = FiretitanSamplingParams(max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
                                              stop=self.renderer.get_stop_sequences())

    def answer(self, messages: list[dict]) -> str:
        prompt = self.renderer.build_generation_prompt(messages)
        result = self.sampler.sample(prompt=prompt, num_samples=1, sampling_params=self.params).result(timeout=900)
        sequence = list(getattr(result, "sequences", []) or [])[0]
        return get_text_content(self.renderer.parse_response(list(sequence.tokens or []))[0])

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
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--variant", action="append", required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    windows = {row["window_id"]: Window.from_dict(row) for row in _rows(args.run / "windows.jsonl")}
    variants = {row["variant_id"]: row for row in _rows(args.run / "variants.jsonl") if row.get("variant_id")}
    model = Model(args.snapshot)
    try:
        results = {}
        for variant_id in args.variant:
            variant = variants[variant_id]
            results[variant_id] = run_loop(model, windows[variant["window_id"]], SceneGraph.model_validate(variant["graph"]))
            print(variant_id, [(a["reward"], a["reason"]) for a in results[variant_id]["attempts"]], flush=True)
    finally:
        model.close()
    args.out.write_text(json.dumps({"snapshot": args.snapshot, "results": results}, indent=2) + "\n")


if __name__ == "__main__":
    main()
