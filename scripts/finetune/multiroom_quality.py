"""Score the finished search targets with the layout-quality reward, and prepare rating pairs.

    score   rescore every gate-accepted target with the current reward -> RUN/targets_scored.jsonl,
            and summarise Q and its terms into RUN/quality_report.json
    pairs   up to PAIR_COUNT pairs of gate-accepted rearrangements of the same variant with different Q,
            for a person to pick the better-looking one -> RUN/rating_pairs.jsonl

Nothing here calls a model or spends money. Both stages skip work already written.

    python scripts/finetune/multiroom_quality.py score --workers 6
    python scripts/finetune/multiroom_quality.py pairs --workers 6
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import pathlib
import random

from multiroom_data import DEFAULT_RUN, _append, _log, _rows, _write_json, checker_for
from standardphysics_agents.redesign import FurnitureMove, RoomEdits
from standardphysics_agents.training import parse_edits, score_completion
from standardphysics_agents.training.edits import edits_json
from standardphysics_agents.training.windows import Window
from standardphysics_contracts import SceneGraph

PAIR_COUNT = 30
LOW_TERM = 0.8
"""A Q term below this is reported as pulling the layout's score down."""
NUDGES = ((15.0, 0.0), (-15.0, 0.0), (30.0, 0.0), (-30.0, 0.0), (90.0, 0.0), (0.0, 0.25), (0.0, -0.25))
"""Turns in degrees and sideways slides in metres tried on one moved piece to make a second layout."""
MIN_Q_GAP = 0.03

_WINDOWS: dict[str, Window] = {}
_VARIANTS: dict[str, dict] = {}


def _load(run: str) -> None:
    for row in _rows(pathlib.Path(run) / "windows.jsonl"):
        _WINDOWS[row["window_id"]] = Window.from_dict(row)
    for row in _rows(pathlib.Path(run) / "variants.jsonl"):
        if row["variant_id"]:
            _VARIANTS[row["variant_id"]] = row


def _room(variant_id: str) -> SceneGraph:
    return SceneGraph.model_validate(_VARIANTS[variant_id]["graph"])


def _score_task(target: dict) -> dict:
    verdict = score_completion(target["target"], _room(target["variant_id"]), checker_for(_WINDOWS[target["window_id"]]))
    return {"variant_id": target["variant_id"], "window_id": target["window_id"], "target": target["target"],
            "verdict": verdict.as_dict()}


def _pool(workers: int, run: pathlib.Path):
    return multiprocessing.get_context("spawn").Pool(workers, initializer=_load, initargs=(str(run),))


def run_score(run: pathlib.Path, workers: int) -> dict:
    done = {row["variant_id"] for row in _rows(run / "targets_scored.jsonl")}
    todo = [row for row in _rows(run / "targets.jsonl") if row["target"] and row["variant_id"] not in done]
    with _pool(workers, run) as pool:
        for row in pool.imap_unordered(_score_task, todo):
            _append(run / "targets_scored.jsonl", row)
            _log(f"scored {row['variant_id']} reward={row['verdict']['reward']} q={(row['verdict']['quality'] or {}).get('q')}")
    report = quality_report([row["verdict"] for row in _rows(run / "targets_scored.jsonl")])
    _write_json(run / "quality_report.json", report)
    return report


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def quality_report(verdicts: list[dict]) -> dict:
    accepted = [v for v in verdicts if v["gate_accepts"] and v["quality"]]
    report = {"targets_scored": len(verdicts), "gate_accepted": len(accepted),
              "mean_reward": _mean([v["reward"] for v in accepted])}
    for term in ("q", "wall", "pairs", "sight"):
        values = [v["quality"][term] for v in accepted]
        report[term] = {"mean": _mean(values),
                        "share_below_0_8": round(sum(1 for x in values if x < LOW_TERM) / len(values), 4)
                        if values else None}
    return report


def _nudged(edits: RoomEdits, index: int, turn: float, slide: float) -> RoomEdits:
    moves = list(edits.moves)
    move = moves[index]
    moves[index] = FurnitureMove(node_id=move.node_id, dx=round(move.dx + slide, 2), dy=round(move.dy, 2),
                                 rotation_degrees=float(round(move.rotation_degrees + turn)))
    return RoomEdits(moves=moves)


def _alternatives(edits: RoomEdits):
    for index in range(len(edits.moves)):
        for turn, slide in NUDGES:
            yield _nudged(edits, index, turn, slide)


def _pair_task(scored: dict) -> dict | None:
    room, checker = _room(scored["variant_id"]), checker_for(_WINDOWS[scored["window_id"]])
    base_q = scored["verdict"]["quality"]["q"]
    best = None
    for alternative in _alternatives(parse_edits(scored["target"])):
        verdict = score_completion(edits_json(alternative), room, checker)
        if not verdict.gate_accepts:
            continue
        gap = abs(verdict.quality["q"] - base_q)
        if gap >= MIN_Q_GAP and (best is None or gap > best[0]):
            best = (gap, edits_json(alternative), verdict.as_dict())
    if best is None:
        return None
    return {"variant_id": scored["variant_id"], "window_id": scored["window_id"],
            "search": {"edits": scored["target"], "verdict": scored["verdict"]},
            "nudged": {"edits": best[1], "verdict": best[2]}}


def _rating_row(index: int, pair: dict, rng: random.Random) -> dict:
    window = _WINDOWS[pair["window_id"]]
    sides = [("search", pair["search"]), ("nudged", pair["nudged"])]
    rng.shuffle(sides)
    return {
        "pair_id": f"rating-{index:02d}", "scan_id": window.scan_id, "window_id": pair["window_id"],
        "variant_id": pair["variant_id"], "starting_graph_revision": _room(pair["variant_id"]).revision,
        "a": {"source": sides[0][0], "edits": sides[0][1]["edits"], "q": sides[0][1]["verdict"]["quality"],
              "reward": sides[0][1]["verdict"]["reward"]},
        "b": {"source": sides[1][0], "edits": sides[1][1]["edits"], "q": sides[1][1]["verdict"]["quality"],
              "reward": sides[1][1]["verdict"]["reward"]},
        "question": "Which rearrangement looks better?", "picked": None,
    }


def _one_per_window(scored: list[dict], seed: int) -> list[dict]:
    rng = random.Random(seed)
    rng.shuffle(scored)
    seen, first, rest = set(), [], []
    for row in scored:
        (rest if row["window_id"] in seen else first).append(row)
        seen.add(row["window_id"])
    return first + rest


def run_pairs(run: pathlib.Path, workers: int, seed: int = 11) -> int:
    _load(str(run))
    scored = [row for row in _rows(run / "targets_scored.jsonl") if row["verdict"]["gate_accepts"]]
    pairs = []
    with _pool(workers, run) as pool:
        for pair in pool.imap(_pair_task, _one_per_window(scored, seed)):
            if pair is not None:
                pairs.append(pair)
                _log(f"pair {len(pairs)} from {pair['variant_id']}")
            if len(pairs) >= PAIR_COUNT:
                pool.terminate()
                break
    rng = random.Random(seed)
    rows = [_rating_row(index, pair, rng) for index, pair in enumerate(pairs)]
    (run / "rating_pairs.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=("score", "pairs"))
    parser.add_argument("--run", type=pathlib.Path, default=DEFAULT_RUN)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    if args.stage == "score":
        print(json.dumps(run_score(args.run, args.workers), indent=2))
    else:
        print(json.dumps({"pairs": run_pairs(args.run, args.workers)}))


if __name__ == "__main__":
    main()
