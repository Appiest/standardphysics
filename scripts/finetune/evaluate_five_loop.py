"""Evaluate up to five measured propose-check-revise attempts per held-out room.

Each gate-accepted proposal becomes the next room. Rejected proposals leave the
room unchanged and return the checker's exact reason. Usability relative to the
original room is logged as a stricter secondary outcome. Only a checker-cleared
final layout counts as a presented fix.

    python scripts/finetune/evaluate_five_loop.py --data runs/finetune/multiroom/v2 \\
        --base-url http://localhost:8080/v1 --model local-qwen --out five-loop.jsonl

Use --split train with a separate output path to collect model attempts for
multiroom_data.py trace-corrections. Held-out is the default benchmark split.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
from collections.abc import Callable

from multiroom_train_data import MultiroomData, load
from standardphysics_agents.fix import apply_moves
from standardphysics_agents.training.edits import node_moves, parse_edits
from standardphysics_agents.training.feedback import measured_feedback_message
from standardphysics_agents.training.prompt import prompt_messages
from standardphysics_agents.training.reward import score_completion
from standardphysics_agents.training.usability import usability

DEFAULT_DATA = pathlib.Path(__file__).resolve().parents[2] / "runs/finetune/multiroom/v2"
MAX_ATTEMPTS = 5
EVALUATION_POLICY = "checker-full-clear-v1"
Sampler = Callable[[list[dict]], str]


def _fixable_left(graph, checker) -> int:
    return len(checker.fixable_problems(checker.assess(graph)))


def _checked_move(completion, baseline, current, checker, verdict):
    if not verdict.gate_accepts:
        return current, None, None
    edits = parse_edits(completion)
    if edits is None:
        raise AssertionError("gate accepted a completion that did not parse")
    candidate = apply_moves(current, node_moves(edits))
    step_usability = usability(current, candidate, current, checker.scenario)
    baseline_usability = usability(baseline, candidate, baseline, checker.scenario)
    return candidate, step_usability, baseline_usability


def _attempt(completion, baseline, current, checker, index, current_baseline_usability):
    verdict = score_completion(completion, current, checker)
    updated, step_usability, candidate_baseline_usability = _checked_move(
        completion, baseline, current, checker, verdict,
    )
    accepted = verdict.gate_accepts
    if accepted:
        assert candidate_baseline_usability is not None
        current_baseline_usability = candidate_baseline_usability
    feedback = measured_feedback_message(
        updated, checker, accepted=accepted, reason=verdict.reason, fixable_left=_fixable_left(updated, checker),
        parsed=verdict.parsed, hard_constraints_pass=verdict.hard_constraints_pass,
        step_usability=step_usability, candidate_baseline_usability=candidate_baseline_usability,
        current_baseline_usability=current_baseline_usability,
    )
    record = {"index": index, "completion": completion, "verdict": verdict.as_dict(),
              "step_usability": step_usability, "candidate_baseline_usability": candidate_baseline_usability,
              "current_baseline_usability": current_baseline_usability, "accepted": accepted,
              "feedback": json.loads(feedback["content"])}
    return updated, current_baseline_usability, record, feedback


def evaluate_variant(data: MultiroomData, row: dict, sampler: Sampler, model: str,
                     max_attempts: int = MAX_ATTEMPTS) -> dict:
    if not 1 <= max_attempts <= MAX_ATTEMPTS:
        raise ValueError("max_attempts must be between one and five")
    variant_id = row["variant"]
    metadata = data.variants[variant_id]
    checker = data.checker(metadata["window_id"])
    current = data.graph(variant_id)
    baseline = current
    baseline_fixable = _fixable_left(current, checker)
    record = {"variant": variant_id, "window_id": metadata["window_id"],
              "scan_id": metadata["scan_id"], "model": model, "baseline_fixable_left": baseline_fixable,
              "eligible": baseline_fixable > 0, "attempt_limit": max_attempts,
              "evaluation_policy": EVALUATION_POLICY, "attempts": []}
    if baseline_fixable == 0:
        return {**record, "success": False, "abstained": True, "final_fixable_left": 0,
                "checker_full_clear_within_five": False, "full_usability_preserved": False,
                "final_baseline_usability": 1.0, "attempts_used": 0, "stop_reason": "no_fixable_findings"}

    messages = prompt_messages(current, checker)
    success = False
    current_baseline_usability = 1.0
    for index in range(1, max_attempts + 1):
        completion = sampler(messages)
        current, current_baseline_usability, attempt, feedback = _attempt(
            completion, baseline, current, checker, index, current_baseline_usability,
        )
        record["attempts"].append(attempt)
        if attempt["accepted"] and attempt["feedback"]["checker_feedback"]["fixable_left"] == 0:
            success = True
            break
        messages.extend(({"role": "assistant", "content": completion}, feedback))

    return {**record, "success": success, "checker_full_clear_within_five": success,
            "full_usability_preserved": success and current_baseline_usability == 1.0,
            "final_baseline_usability": current_baseline_usability,
            "abstained": not success,
            "final_fixable_left": _fixable_left(current, checker),
            "attempts_used": len(record["attempts"]),
            "stop_reason": "fully_cleared" if success else "attempt_limit"}


def _previous_records(data: MultiroomData, rows: list[dict], out: pathlib.Path,
                      model: str, max_attempts: int) -> dict[str, dict]:
    allowed = {row["variant"] for row in rows}
    previous: dict[str, dict] = {}
    if not out.exists():
        return previous
    for line in out.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        variant = record.get("variant")
        if variant not in allowed or variant in previous:
            raise ValueError(f"unknown or duplicate saved variant: {variant}")
        metadata = data.variants[variant]
        expected = {"model": model, "window_id": metadata["window_id"], "scan_id": metadata["scan_id"],
                    "attempt_limit": max_attempts, "evaluation_policy": EVALUATION_POLICY}
        if any(record.get(key) != value for key, value in expected.items()):
            raise ValueError(f"saved variant {variant} has mismatched model, metadata, or attempt limit")
        if record.get("attempts_used") != len(record.get("attempts", [])) or "success" not in record:
            raise ValueError(f"saved variant {variant} is incomplete")
        previous[variant] = record
    return previous


def evaluate(data: MultiroomData, sampler: Sampler, model: str, out: pathlib.Path,
             max_attempts: int = MAX_ATTEMPTS, rows: list[dict] | None = None) -> dict:
    if not 1 <= max_attempts <= MAX_ATTEMPTS:
        raise ValueError("max_attempts must be between one and five")
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = data.heldout if rows is None else rows
    records_by_variant = _previous_records(data, rows, out, model, max_attempts)
    resumed = len(records_by_variant)
    with out.open("a") as handle:
        for row in rows:
            variant = row["variant"]
            if variant in records_by_variant:
                continue
            record = evaluate_variant(data, row, sampler, model, max_attempts)
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            records_by_variant[variant] = record
    records = [records_by_variant[row["variant"]] for row in rows]
    eligible = [record for record in records if record["eligible"]]
    return {"variants": len(records), "eligible_variants": len(eligible),
            "fully_cleared": sum(record["success"] for record in eligible),
            "fully_cleared_with_full_usability": sum(record["full_usability_preserved"] for record in eligible),
            "abstained": sum(record["abstained"] for record in eligible),
            "resumed_variants": resumed, "records": str(out)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=pathlib.Path, default=DEFAULT_DATA)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS)
    parser.add_argument("--split", choices=("heldout", "train"), default="heldout",
                        help="evaluate held-out rooms or collect training-room correction traces")
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()

    from openai import OpenAI

    client = OpenAI(base_url=args.base_url, api_key=os.environ.get(args.api_key_env, "none"))

    def sample(messages: list[dict]) -> str:
        reply = client.chat.completions.create(model=args.model, messages=messages, temperature=args.temperature,
                                               max_tokens=args.max_tokens)
        return reply.choices[0].message.content or ""

    data = load(args.data)
    rows = data.rl if args.split == "train" else data.heldout
    print(json.dumps(evaluate(data, sample, args.model, args.out, args.max_attempts, rows)))


if __name__ == "__main__":
    main()
