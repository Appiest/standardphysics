"""Build synthetic shop training rows while preserving the real multiroom held-out set."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import pathlib

from multiroom_data import (
    Progress,
    _append,
    _prompt_row,
    _rows,
    _write_json,
    checker_for,
    run_targets,
    target_quality,
)
from shop_generator import generate
from standardphysics_agents.training import scramble
from standardphysics_agents.training.scramble import LIGHT
from standardphysics_agents.training.windows import Window


def make_room(index: int) -> Window:
    graph, scenario, _ = generate(index)
    room_name = f"synthetic-{index:04d}"
    return Window(window_id=f"{room_name}:whole", scan_id=str(graph.scan_id), centre=None, graph=graph,
                  scenario=scenario, route="scan")


def run_rooms(run: pathlib.Path, count: int) -> None:
    output = run / "windows.jsonl"
    done = {row["window_id"] for row in _rows(output)}
    for index in range(count):
        window = make_room(index)
        if window.window_id in done:
            continue
        _append(output, window.as_dict())
        _write_json(run / "progress.json", {"stage": "rooms", "done": index + 1, "total": count})
        print(f"room {index + 1}/{count}", flush=True)


def _scramble_task(row: dict) -> list[dict]:
    window = Window.from_dict(row)
    checker = checker_for(window)
    made = scramble(window.graph, checker, 3, seed=int(window.window_id[10:14]), how=LIGHT)
    return [{"variant_id": f"{window.window_id}:{variant.name}", "window_id": window.window_id,
             "scan_id": window.scan_id, "name": variant.name, "fixable": list(variant.fixable),
             "graph": variant.graph.model_dump(mode="json")} for variant in made]


def run_scrambles(run: pathlib.Path, workers: int) -> None:
    output = run / "variants.jsonl"
    done = {row["window_id"] for row in _rows(output)}
    windows = _rows(run / "windows.jsonl")
    todo = [row for row in windows if row["window_id"] not in done]
    with multiprocessing.get_context("spawn").Pool(workers) as pool:
        for rows, window in zip(pool.imap(_scramble_task, todo), todo):
            for row in rows:
                _append(output, row)
            if not rows:
                _append(output, {"variant_id": None, "window_id": window["window_id"]})
            done.add(window["window_id"])
            _write_json(run / "progress.json", {"stage": "scrambles", "done": len(done), "total": len(windows)})
            print(f"scramble {len(done)}/{len(windows)} variants={len(rows)}", flush=True)


def run_dataset(run: pathlib.Path, real: pathlib.Path) -> None:
    if (run / "report.json").exists():
        return
    synthetic_windows = _rows(run / "windows.jsonl")
    synthetic_variants = [row for row in _rows(run / "variants.jsonl") if row["variant_id"]]
    targets = {row["variant_id"]: row for row in _rows(run / "targets.jsonl")}
    real_heldout = _rows(real / "dataset/heldout.jsonl")
    real_ids = {row["variant"] for row in real_heldout}
    real_variants = [row for row in _rows(real / "variants.jsonl") if row["variant_id"] in real_ids]
    real_windows = [row for row in _rows(real / "windows.jsonl")
                    if row["window_id"] in {variant["window_id"] for variant in real_variants}]
    windows = {row["window_id"]: Window.from_dict(row) for row in synthetic_windows}
    sft, rl = [], []
    for variant in synthetic_variants:
        row = _prompt_row(variant, windows[variant["window_id"]], targets.get(variant["variant_id"]))
        rl.append({key: value for key, value in row.items() if key != "target"})
        if "target" in row:
            sft.append({"messages": [*row["messages"], {"role": "assistant", "content": row["target"]}],
                        "variant": row["variant"], "window": row["window"]})
    dataset = run / "dataset"
    dataset.mkdir(exist_ok=True)
    for name, rows in (("sft", sft), ("rl", rl), ("heldout", real_heldout)):
        (dataset / f"{name}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    for name, synthetic, real_rows in (("windows", synthetic_windows, real_windows),
                                       ("variants", _rows(run / "variants.jsonl"), real_variants)):
        (run / f"{name}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in [*synthetic, *real_rows]))
    real_report = json.loads((real / "report.json").read_text())
    _write_json(run / "report.json", {"windows": [{"window_id": row["window_id"], "scan": "synthetic"}
                                                  for row in synthetic_windows] +
                                                 [row for row in real_report["windows"] if row["window_id"]
                                                  in {window["window_id"] for window in real_windows}],
                                        "split": {"totals": {"train": len(synthetic_variants),
                                                             "heldout": len(real_heldout)},
                                                  "by_scan": {}, "held_out": real_report["split"]["held_out"]},
                                        "variants": {"total": len(synthetic_variants) + len(real_variants)},
                                        "targets": target_quality(list(targets.values()))})
    _write_json(run / "progress.json", {"stage": "dataset", "done": True,
                                         "rooms": len(synthetic_windows), "sft": len(sft), "rl": len(rl),
                                         "heldout": len(real_heldout)})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("rooms", "scrambles", "targets", "dataset"))
    parser.add_argument("--run", type=pathlib.Path, required=True)
    parser.add_argument("--real", type=pathlib.Path)
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    args.run.mkdir(parents=True, exist_ok=True)
    if args.stage == "rooms":
        run_rooms(args.run, args.count)
    elif args.stage == "scrambles":
        run_scrambles(args.run, args.workers)
    elif args.stage == "targets":
        run_targets(args.run, args.workers, Progress(args.run / "target_progress.json"))
    else:
        run_dataset(args.run, args.real)


if __name__ == "__main__":
    main()
