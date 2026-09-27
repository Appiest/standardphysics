"""Evaluate up to five measured propose-check-revise attempts per held-out room.

Each gate-accepted proposal becomes the next room. Rejected proposals leave the
room unchanged and return the checker's exact reason. Usability relative to the
original room is logged as a stricter secondary outcome. Only a checker-cleared
final layout counts as a presented fix.

    python scripts/finetune/evaluate_five_loop.py --data runs/finetune/multiroom/v2 \\
        --base-url http://localhost:8080/v1 --model local-qwen --out five-loop.jsonl

Use --split train with a separate output path to collect model attempts for
multiroom_data.py trace-corrections. Held-out is the default benchmark split.

--mode solver-assisted is the product loop: each attempt, the deterministic
repair search (`room_solver.solve`) also answers the current room, and the
checker keeps whichever answer ranks higher, the model's winning ties. Each
attempt records which source it used. --mode solver-only needs no model.

--plan-image attaches a top-down plan of the current room, drawn from the same
JSON, to every message the model reads (`plan_image.py`).

--interface menu replaces raw coordinates with a numbered menu of moves that
are already legal and measured (`training/menu.py`); the model answers
`{"choose":[...],"why":"..."}` and its picks become the same edits JSON the
checker scores. Each menu turn is stateless: the system prompt, the current
room, the menu and the last result, never the whole conversation. Every
attempt records seconds, prompt_tokens and completion_tokens.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from multiroom_train_data import MultiroomData, load
from standardphysics_agents.training.edits import apply_edits, parse_edits
from standardphysics_agents.training.feedback import feedback_message
from standardphysics_agents.training.menu import build_menu, menu_messages, resolve
from standardphysics_agents.training.prompt import prompt_messages
from standardphysics_agents.training.reward import score_completion
from standardphysics_agents.training.usability import usability

DEFAULT_DATA = pathlib.Path(__file__).resolve().parents[2] / "runs/finetune/multiroom/v2"
MAX_ATTEMPTS = 5
EVALUATION_POLICY = "checker-full-clear-v1"
MODES = ("model", "solver-assisted", "solver-only")
INTERFACES = ("raw", "menu")


@dataclass(frozen=True)
class Reply:
    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


Sampler = Callable[[list[dict]], "str | Reply"]
Solver = Callable[[object, object], str | None]
Illustrator = Callable[[dict], dict]


def _fixable_left(graph, checker) -> int:
    return len(checker.fixable_problems(checker.assess(graph)))


def _checked_move(completion, baseline, current, checker, verdict):
    if not verdict.gate_accepts:
        return current, None, None
    edits = parse_edits(completion)
    if edits is None:
        raise AssertionError("gate accepted a completion that did not parse")
    candidate = apply_edits(current, edits)
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
    feedback = feedback_message(
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


def _verdict_rank(verdict) -> tuple:
    return (verdict.gate_accepts, verdict.gate_accepts and verdict.fixable_left == 0,
            -verdict.construction_inches, verdict.shortfall_recovered)


def _ask(sampler: Sampler | None, messages: list[dict]) -> tuple[str, dict]:
    """The model's reply text, and how long it took and how many tokens it used, when the server says."""
    if sampler is None:
        return "", {}
    started = time.monotonic()
    reply = sampler(messages)
    seconds = round(time.monotonic() - started, 2)
    if isinstance(reply, str):
        return reply, {"seconds": seconds, "prompt_tokens": None, "completion_tokens": None}
    return reply.text, {"seconds": seconds, "prompt_tokens": reply.prompt_tokens,
                        "completion_tokens": reply.completion_tokens}


def _preferred(completion: str, current, checker, sampler, solver) -> tuple[str, str]:
    """The answer this attempt submits, and whether the model or the solver wrote it."""
    if solver is None:
        return completion, "model"
    solved = solver(current, checker)
    if solved is None:
        return completion, "model"
    if sampler is None:
        return solved, "solver"
    model_rank = _verdict_rank(score_completion(completion, current, checker))
    solver_rank = _verdict_rank(score_completion(solved, current, checker))
    return (solved, "solver") if solver_rank > model_rank else (completion, "model")


def _unchanged(message: dict) -> dict:
    return message


class RawTurns:
    """The model writes coordinates and reads the whole conversation so far."""

    def __init__(self, current, checker, sampler, solver, illustrate):
        system, room = prompt_messages(current, checker)
        self.messages = [system, illustrate(room)]
        self.checker, self.sampler, self.solver, self.illustrate = checker, sampler, solver, illustrate

    def ask(self, current) -> tuple[str, str, dict]:
        reply, stats = _ask(self.sampler, self.messages)
        completion, source = _preferred(reply, current, self.checker, self.sampler, self.solver)
        return completion, source, stats

    def observe(self, completion: str, attempt: dict, feedback: dict) -> None:
        self.messages.extend(({"role": "assistant", "content": completion}, self.illustrate(feedback)))


class MenuTurns:
    """The model picks from a menu of legal, measured moves; each prompt stands alone."""

    def __init__(self, checker, sampler, illustrate):
        self.checker, self.sampler, self.illustrate = checker, sampler, illustrate
        self.last: dict | None = None
        self.resolution = None

    def ask(self, current) -> tuple[str, str, dict]:
        menu = build_menu(current, self.checker)
        system, room = menu_messages(current, self.checker, menu, self.last)
        reply, stats = _ask(self.sampler, [system, self.illustrate(room)])
        self.resolution = resolve(reply, current, menu, self.checker.pinned)
        return self.resolution.completion, "model", {
            **stats, "reply": reply, "resolution": self.resolution.as_dict(),
            "menu": {"problems": menu.problem_view, "options": [option.as_prompt() for option in menu.options]},
        }

    def observe(self, completion: str, attempt: dict, feedback: dict) -> None:
        self.last = {**self.resolution.as_dict(), "accepted": attempt["accepted"],
                     "reason": attempt["verdict"]["reason"],
                     "fixable_left": attempt["feedback"]["checker_feedback"]["fixable_left"]}


def evaluate_variant(data: MultiroomData, row: dict, sampler: Sampler | None, model: str,
                     max_attempts: int = MAX_ATTEMPTS, solver: Solver | None = None,
                     illustrate: Illustrator = _unchanged, interface: str = "raw") -> dict:
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

    turns = (MenuTurns(checker, sampler, illustrate) if interface == "menu"
             else RawTurns(current, checker, sampler, solver, illustrate))
    success = False
    current_baseline_usability = 1.0
    for index in range(1, max_attempts + 1):
        completion, source, extra = turns.ask(current)
        current, current_baseline_usability, attempt, feedback = _attempt(
            completion, baseline, current, checker, index, current_baseline_usability,
        )
        attempt.update(source=source, **extra)
        record["attempts"].append(attempt)
        if attempt["accepted"] and attempt["feedback"]["checker_feedback"]["fixable_left"] == 0:
            success = True
            break
        turns.observe(completion, attempt, feedback)

    construction = sum(a["verdict"]["construction_inches"] for a in record["attempts"] if a["accepted"])
    return {**record, "success": success, "checker_full_clear_within_five": success,
            "construction_inches": round(construction, 2), "needs_construction": success and construction > 0,
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


def evaluate(data: MultiroomData, sampler: Sampler | None, model: str, out: pathlib.Path,
             max_attempts: int = MAX_ATTEMPTS, rows: list[dict] | None = None, solver: Solver | None = None,
             workers: int = 1, limit: int | None = None, illustrate: Illustrator = _unchanged,
             interface: str = "raw") -> dict:
    if not 1 <= max_attempts <= MAX_ATTEMPTS:
        raise ValueError("max_attempts must be between one and five")
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = data.heldout if rows is None else rows
    records_by_variant = _previous_records(data, rows, out, model, max_attempts)
    resumed = len(records_by_variant)
    pending = [row for row in rows if row["variant"] not in records_by_variant][:limit]
    with out.open("a") as handle, ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(evaluate_variant, data, row, sampler, model, max_attempts, solver, illustrate,
                               interface) for row in pending]
        for future in as_completed(futures):
            record = future.result()
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            records_by_variant[record["variant"]] = record
    records = [records_by_variant[row["variant"]] for row in rows if row["variant"] in records_by_variant]
    eligible = [record for record in records if record["eligible"]]
    return {"variants": len(records), "eligible_variants": len(eligible),
            "fully_cleared": sum(record["success"] for record in eligible),
            "fully_cleared_with_full_usability": sum(record["full_usability_preserved"] for record in eligible),
            "cleared_with_construction": sum(record.get("needs_construction", False) for record in eligible),
            "abstained": sum(record["abstained"] for record in eligible),
            "resumed_variants": resumed, "records": str(out)}


def cached_solver() -> Solver:
    """`room_solver.solve` as a loop answer, remembered per room so an unchanged room is searched once."""
    from room_solver import solve

    answers: dict[str, str | None] = {}

    def answer(current, checker) -> str | None:
        key = current.model_dump_json()
        if key not in answers:
            solution, _ = solve(current, checker)
            answers[key] = solution.completion if solution else None
        return answers[key]

    return answer


def _sampler(args) -> Sampler | None:
    if args.mode == "solver-only":
        return None
    if args.fireworks:
        from fireworks_sampler import FireworksSampler

        return FireworksSampler(args.fireworks, args.spend_file, args.temperature, args.max_tokens)
    from openai import OpenAI

    client = OpenAI(base_url=args.base_url, api_key=os.environ.get(args.api_key_env, "none"))

    def sample(messages: list[dict]) -> Reply:
        reply = client.chat.completions.create(model=args.served_model or args.model, messages=messages,
                                               temperature=args.temperature,
                                               max_tokens=args.max_tokens)
        usage = reply.usage
        return Reply(reply.choices[0].message.content or "", getattr(usage, "prompt_tokens", None),
                     getattr(usage, "completion_tokens", None))

    return sample


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=pathlib.Path, default=DEFAULT_DATA)
    parser.add_argument("--base-url", help="an OpenAI-compatible server, such as a local MLX server")
    parser.add_argument("--fireworks", help="sample Fireworks serverless: `base` or a saved training state reference")
    parser.add_argument("--spend-file", type=pathlib.Path, help="where --fireworks records its estimated spend")
    parser.add_argument("--model", required=True, help="label for the records; the served model name otherwise")
    parser.add_argument("--served-model", help="the name the --base-url server knows the model by, when it differs")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--plan-image", action="store_true", help="show the model a drawn plan of the room each turn")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS)
    parser.add_argument("--mode", choices=MODES, default="model")
    parser.add_argument("--interface", choices=INTERFACES, default="raw",
                        help="raw: the model writes coordinates; menu: it picks from legal, measured moves")
    parser.add_argument("--split", choices=("heldout", "train"), default="heldout",
                        help="evaluate held-out rooms or collect training-room correction traces")
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--workers", type=int, default=1, help="rooms evaluated at once")
    parser.add_argument("--limit", type=int, help="evaluate at most this many new rooms, then exit and free memory")
    parser.add_argument("--shard", default="0/1", help="i/n: evaluate every n-th room starting at i, for parallel processes")
    args = parser.parse_args()
    if args.mode != "solver-only" and not (args.base_url or args.fireworks):
        parser.error("--base-url or --fireworks is required unless --mode solver-only")
    if args.fireworks and not args.spend_file:
        parser.error("--fireworks needs --spend-file so the watchdog can meter it")
    if args.interface == "menu" and args.mode != "model":
        parser.error("--interface menu is for --mode model; the solver answers in raw edits")

    data = load(args.data)
    rows = data.rl if args.split == "train" else data.heldout
    index, count = (int(part) for part in args.shard.split("/"))
    rows = rows[index::count]
    solver = cached_solver() if args.mode != "model" else None
    illustrate = _unchanged
    if args.plan_image:
        from plan_image import with_plan as illustrate
    print(json.dumps(evaluate(data, _sampler(args), args.model, args.out, args.max_attempts, rows, solver,
                              args.workers, args.limit, illustrate, args.interface)))


if __name__ == "__main__":
    main()
