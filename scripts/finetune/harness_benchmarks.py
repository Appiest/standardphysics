"""Benchmarks for the harness run, read from the saved evaluation answers; no model calls, no spend.

For every evaluated model and each held-out source (real scans, generated rooms)
it reports what the reward saw and two things the solver hides:

    raw_legal       the share of parsed answers whose moves, applied exactly as
                    written, already break no hard constraint: the model's own
                    spatial accuracy, before snapping helps it
    raw_seats_face  of the seats an answer moves, the share that already face
                    what they serve as written: rotation awareness without the
                    solver turning them

Verdicts the trainer saved are reused, so only the raw checks are computed
here. `--previous` adds last night's model, whose saved answers are re-scored
under the current reward, since they were scored under an older one.

    python scripts/finetune/harness_benchmarks.py --run runs/finetune/harness \\
        --previous ~/sp-finetune/repo/runs/finetune/multiroom/v2/qwen3p8-27b/eval/rl.jsonl
"""

from __future__ import annotations

import argparse
import json
import pathlib
from collections import defaultdict

from arkit_dataset import ARKIT_HELDOUT
from multiroom_data import checker_for
from standardphysics_agents.fix import apply_moves, violations
from standardphysics_agents.snap import facing_error_degrees
from standardphysics_agents.snap.facing import is_seat
from standardphysics_agents.training import parse_edits, score_completion
from standardphysics_agents.training.edits import node_moves
from standardphysics_agents.training.usefulness import FACING_TOLERANCE_DEGREES
from standardphysics_agents.training.windows import Window
from standardphysics_contracts import SceneGraph
from synthetic_data import HELDOUT_PREFIX


def _rows(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def source_of(window_id: str) -> str:
    if window_id.startswith(ARKIT_HELDOUT):
        return "arkit homes"
    return "generated" if window_id.startswith(HELDOUT_PREFIX) else "real scans"


def raw_checks(completion: str, graph: SceneGraph) -> tuple[bool | None, list[bool]]:
    """(raw request legal, per moved seat whether it faces what it serves as written)."""
    edits = parse_edits(completion)
    if edits is None or not edits.moves:
        return None, []
    known = {node.id for node in graph.nodes}
    if not {move.node_id for move in edits.moves} <= known:
        return None, []
    raw = apply_moves(graph, node_moves(edits))
    moved = {move.node_id for move in edits.moves}
    faces = []
    for node in raw.nodes:
        if node.id in moved and is_seat(node):
            error = facing_error_degrees(node, raw)
            if error is not None:
                faces.append(error <= FACING_TOLERANCE_DEGREES)
    return not violations(graph, raw), faces


def _share(flags: list) -> float | None:
    return round(sum(flags) / len(flags), 4) if flags else None


def _mean(values: list) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def summarize(records: list[dict]) -> dict:
    by_variant = defaultdict(list)
    for record in records:
        by_variant[record["variant"]].append(record["verdict"]["gate_accepts"])
    legal = [r["raw_legal"] for r in records if r["raw_legal"] is not None]
    faces = [flag for r in records for flag in r["raw_faces"]]
    verdicts = [r["verdict"] for r in records]
    return {
        "answers": len(records),
        "variants": len(by_variant),
        "parse_rate": _share([v["parsed"] for v in verdicts]),
        "accepted": _share([v["gate_accepts"] for v in verdicts]),
        "best_of_k_accepted": _share([any(flags) for flags in by_variant.values()]),
        "all_fixable_cleared": _share([v["gate_accepts"] and v["fixable_left"] == 0 for v in verdicts]),
        "mean_reward": _mean([v["reward"] for v in verdicts]),
        "mean_recovered_when_accepted": _mean([v["shortfall_recovered"] for v in verdicts if v["gate_accepts"]]),
        "mean_usefulness_when_accepted": _mean([(v.get("usefulness") or {}).get("score", 0) for v in verdicts
                                                if v["gate_accepts"] and v.get("usefulness")]),
        "mean_snapped_meters": _mean([v["snapped_meters"] for v in verdicts if v["parsed"]]),
        "refused_by_directive": _share([v["reason"].startswith("precedent_violation") for v in verdicts]),
        "refused_as_less_useful": _share([v["reason"].startswith("less_useful") for v in verdicts]),
        "raw_legal": _share(legal),
        "raw_seats_face": _share(faces),
    }


VERDICT_KEYS = ("reward", "parsed", "hard_constraints_pass", "gate_accepts", "shortfall_recovered", "fixable_left",
                "reason", "usefulness", "snapped_meters")
"""What the trainer already saved for every evaluation answer; re-scoring them is the slow part."""


class Rooms:
    def __init__(self, run: pathlib.Path):
        self.windows = {row["window_id"]: Window.from_dict(row) for row in _rows(run / "windows.jsonl")}
        self.variants = {row["variant_id"]: row for row in _rows(run / "variants.jsonl") if row.get("variant_id")}
        self._checkers: dict = {}

    def score(self, record: dict, rescore: bool) -> dict | None:
        """One answer's verdict and raw checks; the saved verdict is reused unless `rescore` asks otherwise."""
        variant = self.variants.get(record["variant"])
        if variant is None:
            return None
        window = self.windows[variant["window_id"]]
        graph = SceneGraph.model_validate(variant["graph"])
        verdict = self._verdict(record, window, graph) if rescore else {k: record.get(k) for k in VERDICT_KEYS}
        legal, faces = raw_checks(record["completion"], graph)
        return {"variant": record["variant"], "source": source_of(window.window_id), "verdict": verdict,
                "raw_legal": legal, "raw_faces": faces}

    def _verdict(self, record: dict, window: Window, graph: SceneGraph) -> dict:
        if window.window_id not in self._checkers:
            self._checkers[window.window_id] = checker_for(window)
        return score_completion(record["completion"], graph, self._checkers[window.window_id]).as_dict()


def benchmark(rooms: Rooms, answers: list[dict], rescore: bool = False) -> dict:
    scored = [found for found in (rooms.score(record, rescore) for record in answers) if found]
    groups = defaultdict(list)
    for record in scored:
        groups[record["source"]].append(record)
    return {source: summarize(records) for source, records in sorted(groups.items())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=pathlib.Path, required=True)
    parser.add_argument("--previous", type=pathlib.Path, help="last night's saved evaluation answers")
    parser.add_argument("--out", type=pathlib.Path)
    args = parser.parse_args()
    rooms = Rooms(args.run)
    evals = args.run / "qwen3p8-27b" / "eval"
    report = {path.stem: benchmark(rooms, _rows(path)) for path in sorted(evals.glob("*.jsonl"))}
    if args.previous:
        report["previous_night_rl"] = benchmark(rooms, _rows(args.previous), rescore=True)
    out = args.out or args.run / "benchmarks.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
