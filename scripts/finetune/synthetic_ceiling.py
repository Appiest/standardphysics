"""Resumable, wider deterministic search over the multiroom v2 held-out variants."""

from __future__ import annotations

import argparse
import multiprocessing
import pathlib
from collections import Counter

from multiroom_data import _append, _rows, _write_json, checker_for, put_back_record
from standardphysics_agents.fix import apply_moves, propose_fix
from standardphysics_agents.training import edits_between, edits_json, score_completion
from standardphysics_agents.training.edits import node_moves, parse_edits
from standardphysics_agents.training.usability import usability
from standardphysics_agents.training.windows import Window
from standardphysics_contracts import SceneGraph


def _search(graph: SceneGraph, checker, limit: int, rounds: int) -> tuple[dict | None, Counter]:
    rejected: Counter = Counter()
    layout = graph

    def pinned(before, after) -> str | None:
        return "pinned pieces" if any(before.by_id(node_id).transform != after.by_id(node_id).transform
                                      for node_id in checker.pinned) else None

    for _ in range(rounds):
        before = checker.assess(layout)
        outcome = propose_fix(layout, checker.scenario, checker.measure, checker.fixable_problems(before),
                              rules=checker.rules, ledger=checker.ledger, baseline=before,
                              max_tier=checker.max_tier, limit=limit, offer_relaxation=False,
                              candidate_rejection=pinned)
        rejected.update(outcome.rejected)
        if not outcome.found:
            break
        layout = outcome.graph
    edits = edits_between(graph, layout)
    if not edits.moves:
        return None, rejected
    completion = edits_json(edits)
    verdict = score_completion(completion, graph, checker).as_dict()
    if verdict["gate_accepts"]:
        verdict["step_usability"] = _step_usability(graph, checker, completion)
    return verdict, rejected


def _step_usability(graph: SceneGraph, checker, completion: str) -> float:
    edits = parse_edits(completion)
    if edits is None:
        raise AssertionError("accepted edits did not parse")
    candidate = apply_moves(graph, node_moves(edits))
    return usability(graph, candidate, graph, checker.scenario)


def _reason(graph, checker, rejections: Counter) -> str:
    findings = checker.fixable_problems(checker.assess(graph))
    if not findings:
        return "fixed fixtures or non-rearrangeable finding"
    if any("pinned" in reason for reason in rejections):
        return "pinned pieces"
    if any("floor" in reason or "boundary" in reason for reason in rejections):
        return "60 in cap or floor boundary"
    if any("route" in reason or "path" in reason for reason in rejections):
        return "no route"
    return "no accepted route under the bounded search; not a proof of impossibility"


def _task(task: tuple[dict, dict]) -> dict:
    variant_row, window_row = task
    window = Window.from_dict(window_row)
    checker = checker_for(window)
    graph = SceneGraph.model_validate(variant_row["graph"])
    before = checker.assess(graph)
    options = []
    put_back = put_back_record(graph, window.graph, checker)
    if put_back:
        verdict = put_back["verdict"]
        if verdict["gate_accepts"]:
            verdict["step_usability"] = _step_usability(graph, checker, put_back["edits"])
        options.append(verdict)
    wide, rejected = _search(graph, checker, 96, 8)
    if wide:
        options.append(wide)
    accepted = [option for option in options if option["gate_accepts"]]
    best = max(accepted, key=lambda option: option["shortfall_recovered"], default=None)
    return {"variant_id": variant_row["variant_id"], "scan_id": variant_row["scan_id"],
            "fixable": bool(accepted), "best_shortfall_recovered": best["shortfall_recovered"] if best else 0,
            "all_clear": any(option["fixable_left"] == 0 for option in accepted),
            "usable_all_clear": any(option["fixable_left"] == 0 and option.get("step_usability") == 1.0
                                    for option in accepted),
            "reason": None if best else _reason(graph, checker, rejected),
            "baseline_fixable_findings": len(checker.fixable_problems(before)),
            "rejections": dict(rejected)}


def run(source: pathlib.Path, destination: pathlib.Path, workers: int) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / "ceiling.jsonl"
    completed = {row["variant_id"] for row in _rows(output)}
    heldout = {row["variant"] for row in _rows(source / "dataset/heldout.jsonl")}
    windows = {row["window_id"]: row for row in _rows(source / "windows.jsonl")}
    tasks = [(row, windows[row["window_id"]]) for row in _rows(source / "variants.jsonl")
             if row["variant_id"] in heldout and row["variant_id"] not in completed]
    progress = destination / "progress.json"
    total = len(completed) + len(tasks)
    _write_json(progress, {"stage": "ceiling", "done": len(completed), "total": total})
    with multiprocessing.get_context("spawn").Pool(workers) as pool:
        for result in pool.imap_unordered(_task, tasks):
            _append(output, result)
            completed.add(result["variant_id"])
            _write_json(progress, {"stage": "ceiling", "done": len(completed), "total": total})
            print(f"ceiling {len(completed)}/{len(heldout)} {result['variant_id']}", flush=True)
    rows = _rows(output)
    _write_json(destination / "ceiling.json", {"searched": len(rows), "fixable": sum(r["fixable"] for r in rows),
                                                   "rooms": rows})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=pathlib.Path, required=True)
    parser.add_argument("--destination", type=pathlib.Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    run(args.source, args.destination, args.workers)


if __name__ == "__main__":
    main()
