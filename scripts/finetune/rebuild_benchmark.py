"""Rebuild benchmark: shuffle held-out rooms into measured ADA failures, then count model loops to clear them.

The ledger reports measured failures, unknown measurements, and legal sign-off separately. A cleared room is
not described as verified ADA-compliant unless the ledger accepts it for a final layout.

    shuffles   for each measured-clear held-out room, generate independent seeded shuffles of up to six pieces.
               Keep a seed only when its layout passes hard constraints and adds a measured failure (CPU only)
    run        each model rebuilds every shuffle in up to `MAX_LOOPS` loops. A loop is one answer: an answer the
               gate accepts becomes the room, and the next loop starts from a fresh prompt of it plus what the
               ledger still fails; a refused answer gets a reply saying why, plus what the ledger still fails.
               The rebuild stops when the ledger has no measured failures. Rounds run all rooms at once,
               and a round that could pass `--budget` is not started
    report     per model and room group

    python scripts/finetune/rebuild_benchmark.py shuffles --run runs/finetune/arkit \\
        --out runs/finetune/arkit/rebuild --seeds-per-room 5 --seed 42
    python scripts/finetune/rebuild_benchmark.py run --out runs/finetune/arkit/rebuild --model base \\
        --model sft=<training state reference> --budget 20
    python scripts/finetune/rebuild_benchmark.py report --out runs/finetune/arkit/rebuild
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import pathlib
import re
import statistics
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from progress import Spend
from rebuild_judging import feedback, ledger, measured_failures, measures, room, step, with_note
from rebuild_judging import final as final_measures
from standardphysics_agents.training import prompt_messages, scramble
from standardphysics_agents.training.scramble import SHUFFLE
from standardphysics_contracts import SceneGraph

DEFAULT_SEEDS_PER_ROOM = 5
SHUFFLE_TRIES = 6
MAX_LOOPS = 5


def source_of(window_id: str) -> str:
    if window_id.startswith("arkit-heldout-"):
        return "arkit homes"
    if window_id.startswith("synthetic-heldout-"):
        return "generated"
    return "real scans"


def _rows(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def _write(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


# --- shuffles ---------------------------------------------------------------


def _trial_seed(master_seed: int, window_id: str, index: int) -> int:
    return zlib.crc32(f"{master_seed}:{window_id}:{index}".encode())


def _shuffle_task(task: tuple[dict, int, int]) -> tuple[str, list[dict]]:
    window_row, master_seed, seeds_per_room = task
    window, checker = room(window_row)
    owner_result = ledger(window.graph, window, checker)
    owner_failures = {failure.key for failure in measured_failures(window.graph, window, checker)}
    if owner_failures:
        return "preexisting_measured_failures", []
    made = []
    seen = set()
    for index in range(seeds_per_room):
        seed = _trial_seed(master_seed, window.window_id, index)
        for variant in scramble(window.graph, checker, SHUFFLE_TRIES, seed=seed, how=SHUFFLE):
            signature = tuple((str(node.id), tuple(node.transform.m)) for node in variant.graph.nodes if node.movable)
            if signature in seen:
                continue
            start_failures = measured_failures(variant.graph, window, checker)
            if not start_failures:
                continue
            seen.add(signature)
            made.append({"shuffle_id": f"{window.window_id}:seed{master_seed}:{index:03d}",
                         "window_id": window.window_id, "group": source_of(window.window_id),
                         "master_seed": master_seed, "seed_index": index, "seed": seed,
                         "shuffle_attempt": variant.name,
                         "owner_failures": sorted(owner_failures),
                         "owner_measured_unknown": list(owner_result.measured_unknown),
                         "owner_verified_for_final_layout": owner_result.accept_for_final_layout,
                         "added": [failure.key for failure in start_failures],
                         "start_failing": [failure.key for failure in start_failures],
                         "graph": variant.graph.model_dump(mode="json"),
                         "start": measures(window.graph, variant.graph, checker)})
            break
    return ("qualified" if made else "no_qualifying_shuffle"), made


def shuffles(run: pathlib.Path, out: pathlib.Path, workers: int, seeds_per_room: int, master_seed: int) -> dict:
    if seeds_per_room <= 0:
        raise ValueError("--seeds-per-room must be positive")
    if not (run / "dataset" / "heldout.jsonl").exists() or not (run / "windows.jsonl").exists():
        raise FileNotFoundError("the training run needs dataset/heldout.jsonl and windows.jsonl")
    if any(path.stem not in {"shuffles", "windows"} for path in out.glob("*.jsonl")):
        raise ValueError("this output already has model results; use a fresh --out to regenerate shuffles")
    held = {row["window"] for row in _rows(run / "dataset" / "heldout.jsonl")}
    windows = [row for row in _rows(run / "windows.jsonl") if row["window_id"] in held]
    tasks = [(window, master_seed, seeds_per_room) for window in windows]
    with multiprocessing.get_context("spawn").Pool(workers) as pool:
        outcomes = list(pool.imap_unordered(_shuffle_task, tasks))
    made = [row for _, rows in outcomes for row in rows]
    made.sort(key=lambda row: row["shuffle_id"])
    _write(out / "windows.jsonl", windows)
    _write(out / "shuffles.jsonl", made)
    rooms = defaultdict(set)
    for row in made:
        rooms[row["group"]].add(row["window_id"])
    eligible_rooms = sum(status != "preexisting_measured_failures" for status, _ in outcomes)
    summary = {"held_out_rooms": len(windows), "shuffles": len(made), "master_seed": master_seed,
               "seeds_per_room": seeds_per_room, "eligible_seed_slots": eligible_rooms * seeds_per_room,
               "room_selection": dict(Counter(status for status, _ in outcomes)),
               "rooms_with_a_shuffle": {group: len(ids) for group, ids in sorted(rooms.items())}}
    (out / "selection.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


# --- run --------------------------------------------------------------------


@dataclass
class Rebuild:
    shuffle: dict
    messages: list[dict]
    current: dict
    loops: list[dict] = field(default_factory=list)
    reached_at: int | None = None
    stopped: str | None = None

    @property
    def active(self) -> bool:
        return self.reached_at is None and self.stopped is None

    def advance(self, loop: int, completion: str, judged: dict) -> None:
        verdict = judged["verdict"]
        self.loops.append({"loop": loop, "completion": completion, "reward": verdict["reward"],
                           "reason": verdict["reason"], "accepted": verdict["gate_accepts"],
                           "failing": judged["failing"]})
        if "layout" in judged:
            self.current = judged["layout"]
        if not judged["failing"]:
            self.reached_at = loop
        elif "layout" in judged:
            self.messages = judged["next_messages"]
        else:
            self.messages = [*self.messages, {"role": "assistant", "content": completion},
                             {"role": "user", "content": judged["reply"]}]


class BudgetReached(RuntimeError):
    pass


class Meter:
    """Pessimistic spend across every model in the run: a round is reserved at its longest before it starts."""

    def __init__(self, path: pathlib.Path, budget: float, sample_cap: int):
        previous = json.loads(path.read_text()) if path.exists() else {}
        self.path, self.budget, self.sample_cap = path, budget, sample_cap
        self.spend = Spend(prefill_tokens=previous.get("prefill_tokens", 0),
                           sample_tokens=previous.get("sample_tokens", 0))

    def reserve(self, prompt_tokens: int, answers: int) -> None:
        pending = Spend(prefill_tokens=self.spend.prefill_tokens + 2 * prompt_tokens,
                        sample_tokens=self.spend.sample_tokens + 2 * answers * self.sample_cap)
        if pending.dollars > self.budget:
            raise BudgetReached(f"next round could reach ${pending.dollars:.2f} of ${self.budget:.2f}")

    def charge(self, prompt_tokens: int, sample_tokens: int) -> None:
        self.spend.prefill_tokens += prompt_tokens
        self.spend.sample_tokens += sample_tokens
        self.path.write_text(json.dumps(self.spend.as_dict()) + "\n")

    def charge_answers(self, prompts: list, answers: list) -> None:
        prompt_tokens = sum(prompt.length * answer.attempts for prompt, answer in zip(prompts, answers))
        sample_tokens = sum(answer.sample_tokens + (answer.attempts - 1) * self.sample_cap for answer in answers)
        self.charge(prompt_tokens, sample_tokens)


def _round(model, pool, rebuilds: list[Rebuild], windows: dict, meter: Meter, loop: int) -> None:
    prompts = model.prompts([rebuild.messages for rebuild in rebuilds])
    prompt_tokens = sum(prompt.length for prompt in prompts)
    meter.reserve(prompt_tokens, len(prompts))
    answers = model.answer_all(prompts)
    meter.charge_answers(prompts, answers)
    jobs = [(windows[rebuild.shuffle["window_id"]], rebuild.current, answer.text)
            for rebuild, answer in zip(rebuilds, answers)]
    for rebuild, answer, judged in zip(rebuilds, answers, pool.starmap(step, jobs)):
        rebuild.advance(loop, answer.text, judged)


def rebuild_all(model, pool, shuffled: list[dict], windows: dict, meter: Meter) -> list[Rebuild]:
    rebuilds = [Rebuild(row, prompt_messages_for(row, windows), row["graph"]) for row in shuffled]
    for loop in range(1, MAX_LOOPS + 1):
        active = [rebuild for rebuild in rebuilds if rebuild.active]
        if not active:
            break
        try:
            _round(model, pool, active, windows, meter, loop)
        except BudgetReached as reached:
            for rebuild in active:
                rebuild.stopped = str(reached)
            break
        print(f"loop {loop}: {sum(1 for r in rebuilds if r.reached_at)} of {len(rebuilds)} rooms measured-clear; "
              f"${meter.spend.dollars:.2f} spent", flush=True)
    for rebuild in rebuilds:
        rebuild.stopped = rebuild.stopped or (None if rebuild.reached_at else f"measured failures after {MAX_LOOPS} loops")
    return rebuilds


def prompt_messages_for(row: dict, windows: dict) -> list[dict]:
    window, checker = room(windows[row["window_id"]])
    graph = SceneGraph.model_validate(row["graph"])
    return with_note(prompt_messages(graph, checker), feedback(measured_failures(graph, window, checker)))


def _record(model_name: str, state: str | None, rebuild: Rebuild, finished: dict) -> dict:
    row = rebuild.shuffle
    return {"model": model_name, "state": state, "shuffle_id": row["shuffle_id"],
            "master_seed": row["master_seed"], "seed_index": row["seed_index"], "seed": row["seed"],
            "shuffle_attempt": row["shuffle_attempt"],
            "window_id": row["window_id"],
            "group": row["group"], "owner_failures": row["owner_failures"],
            "owner_measured_unknown": row["owner_measured_unknown"],
            "owner_verified_for_final_layout": row["owner_verified_for_final_layout"],
            "added": row["added"], "start_failing": row["start_failing"], "start": row["start"],
            "measured_clear_at": rebuild.reached_at, "stopped": rebuild.stopped,
            "loops": rebuild.loops, "final": finished}


def run(out: pathlib.Path, models: list[tuple[str, str | None]], budget: float, workers: int) -> None:
    shuffled = _rows(out / "shuffles.jsonl")
    windows = {row["window_id"]: row for row in _rows(out / "windows.jsonl")}
    if not shuffled or not windows:
        raise ValueError("generate qualifying shuffles before running models")
    if not models:
        raise ValueError("at least one --model is required")
    if budget <= 0:
        raise ValueError("--budget must be positive")
    if len({name for name, _ in models}) != len(models):
        raise ValueError("model names must be unique")
    from rebuild_loop import MAX_TOKENS, Model  # the Fireworks SDK, which only the sampling machine has
    meter = Meter(out / "spend.json", budget, MAX_TOKENS)
    with multiprocessing.get_context("spawn").Pool(workers) as pool:
        for name, state in models:
            result_path = out / f"{name}.jsonl"
            if result_path.exists():
                prior = _rows(result_path)
                if {row["shuffle_id"] for row in prior} != {row["shuffle_id"] for row in shuffled} or any(
                    row.get("state") != state for row in prior
                ):
                    raise ValueError(f"{result_path} belongs to a different state or shuffle set")
                continue
            model = Model(state)
            try:
                rebuilds = rebuild_all(model, pool, shuffled, windows, meter)
            finally:
                model.close()
            finals = pool.starmap(final_measures, [(windows[r.shuffle["window_id"]], r.current,
                                                    r.shuffle["owner_failures"])
                                                   for r in rebuilds])
            _write(result_path, [_record(name, state, r, f) for r, f in zip(rebuilds, finals)])


# --- report -----------------------------------------------------------------


def _mean(values: list) -> float | None:
    values = [value for value in values if value is not None]
    return round(sum(values) / len(values), 4) if values else None


def _share(flags: list[bool]) -> float | None:
    return round(sum(flags) / len(flags), 4) if flags else None


def _distance(rows: list[dict]) -> dict:
    """Distance from the owner's layout only when that layout has passed legal review."""
    compliant = [row for row in rows if row["owner_verified_for_final_layout"]]
    return {"rooms": len(compliant),
            "at_home_start": _mean([row["start"]["distance"]["at_home"] for row in compliant]),
            "at_home_final": _mean([row["final"]["distance"]["at_home"] for row in compliant]),
            "meters_start": _mean([row["start"]["distance"]["mean_meters"] for row in compliant]),
            "meters_final": _mean([row["final"]["distance"]["mean_meters"] for row in compliant]),
            "verified_for_final_layout": _share([row["final"]["verified_for_final_layout"] for row in compliant])}


def summarize(rows: list[dict]) -> dict:
    reached = [row["measured_clear_at"] for row in rows if row["measured_clear_at"]]
    kept = [row["measured_clear_at"] is not None and not row["final"]["usefulness_lost"]
            and row["final"]["amenity_usability"] >= 1 for row in rows]
    return {
        "rebuilds": len(rows),
        "measured_clear": _share([row["measured_clear_at"] is not None for row in rows]),
        "measured_clear_within": {loops: _share([bool(row["measured_clear_at"]) and
                                            row["measured_clear_at"] <= loops for row in rows])
                        for loops in (1, 2, 3, MAX_LOOPS)},
        "median_loops_when_clear": statistics.median(reached) if reached else None,
        "clear_with_amenities_preserved": _share(kept),
        "verified_for_final_layout": _share([row["final"]["verified_for_final_layout"] for row in rows]),
        "unknown_measurements_final": _mean([len(row["final"]["measured_unknown"]) for row in rows]),
        "ledger_failures": {"owner": _mean([len(row["owner_failures"]) for row in rows]),
                            "shuffled": _mean([len(row["start_failing"]) for row in rows]),
                            "final": _mean([row["final"]["failing_total"] for row in rows])},
        "usefulness": {"owner": _mean([row["final"]["usefulness_owner"]["score"] for row in rows]),
                       "shuffled": _mean([row["start"]["usefulness"]["score"] for row in rows]),
                       "final": _mean([row["final"]["usefulness"]["score"] for row in rows])},
        "look": {"shuffled": _mean([row["start"]["look"]["q"] for row in rows]),
                 "final": _mean([row["final"]["look"]["q"] for row in rows])},
        "amenity_usability": {"shuffled": _mean([row["start"]["amenity_usability"] for row in rows]),
                              "final": _mean([row["final"]["amenity_usability"] for row in rows])},
        "distance_when_owner_compliant": _distance(rows),
        "answers_refused": _share([not loop["accepted"] for row in rows for loop in row["loops"]]),
        "stopped_by_budget": sum(1 for row in rows if row["stopped"] and "$" in row["stopped"]),
    }


def report(out: pathlib.Path) -> dict:
    result: dict[str, object] = {}
    selection = out / "selection.json"
    result["selection"] = json.loads(selection.read_text()) if selection.exists() else None
    for path in sorted(out.glob("*.jsonl")):
        if path.stem in {"shuffles", "windows"}:
            continue
        rows = _rows(path)
        groups = defaultdict(list)
        for row in rows:
            groups[row["group"]].append(row)
        result[path.stem] = {"all": summarize(rows), **{group: summarize(g) for group, g in sorted(groups.items())}}
    spend = out / "spend.json"
    result["spend"] = json.loads(spend.read_text()) if spend.exists() else None
    (out / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def _model(text: str) -> tuple[str, str | None]:
    name, _, state = text.partition("=")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
        raise argparse.ArgumentTypeError("model name must use letters, digits, hyphens, or underscores")
    if (name == "base") == bool(state):
        raise argparse.ArgumentTypeError("use --model base or --model NAME=STATE for a trained model")
    return name, state or None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("shuffles", "run", "report"))
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--run", type=pathlib.Path, help="the training run whose held-out rooms to shuffle")
    parser.add_argument("--model", type=_model, action="append", default=[],
                        help="NAME for the base model or NAME=STATE for a saved training state")
    parser.add_argument("--budget", type=float, help="maximum estimated sampling spend in US dollars")
    parser.add_argument("--workers", type=int, default=max(1, multiprocessing.cpu_count() - 1))
    parser.add_argument("--seeds-per-room", type=int, default=DEFAULT_SEEDS_PER_ROOM)
    parser.add_argument("--seed", type=int, default=0, help="master seed for reproducible room shuffles")
    args = parser.parse_args()
    if args.stage == "shuffles":
        if args.run is None:
            parser.error("shuffles requires --run")
        print(json.dumps(shuffles(args.run, args.out, args.workers, args.seeds_per_room, args.seed), indent=2))
    elif args.stage == "run":
        if args.budget is None:
            parser.error("run requires an explicit --budget")
        run(args.out, args.model, args.budget, args.workers)
    print(json.dumps(report(args.out), indent=2))


if __name__ == "__main__":
    main()
