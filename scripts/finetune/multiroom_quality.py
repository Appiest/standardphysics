"""Score the finished search targets with the layout-quality reward, and prepare rating pairs.

    score   rescore every gate-accepted target with the current reward -> RUN/targets_scored.jsonl,
            and summarise Q and its terms into RUN/quality_report.json

Rating pairs are built by rating_pairs.py. Nothing here calls a model or spends
money, and a rerun skips targets already scored.

    python scripts/finetune/multiroom_quality.py score --workers 6
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import pathlib

from multiroom_data import DEFAULT_RUN, _append, _log, _rows, _write_json, checker_for
from standardphysics_agents.training import score_completion
from standardphysics_agents.training.windows import Window
from standardphysics_contracts import SceneGraph

LOW_TERM = 0.8
"""A Q term below this is reported as pulling the layout's score down."""

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=("score",))
    parser.add_argument("--run", type=pathlib.Path, default=DEFAULT_RUN)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    print(json.dumps(run_score(args.run, args.workers), indent=2))


if __name__ == "__main__":
    main()
