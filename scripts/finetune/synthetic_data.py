"""Build synthetic shop training rows while preserving the real multiroom held-out set."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import pathlib
import zlib

from multiroom_data import (
    Progress,
    _append,
    _prompt_row,
    _rows,
    _write_json,
    checker_for,
    run_corrections,
    run_targets,
    target_quality,
)
from shop_generator import generate
from standardphysics_agents.training import scramble
from standardphysics_agents.training.scramble import LIGHT
from standardphysics_agents.training.windows import Window
from standardphysics_contracts.precedents import SpaceTypology

SPACE_TYPES = {
    "cafe": SpaceTypology.QSR_BEVERAGE, "boba tea shop": SpaceTypology.QSR_BEVERAGE,
    "bakery": SpaceTypology.QSR_BEVERAGE, "ice cream parlor": SpaceTypology.QSR_BEVERAGE,
    "restaurant": SpaceTypology.RESTAURANT_DINING, "fast food restaurant": SpaceTypology.RESTAURANT_DINING,
    "boutique": SpaceTypology.COMMERCIAL_RETAIL, "bookstore": SpaceTypology.COMMERCIAL_RETAIL,
    "convenience store": SpaceTypology.COMMERCIAL_RETAIL, "pharmacy": SpaceTypology.COMMERCIAL_RETAIL,
    "salon": SpaceTypology.COMMERCIAL_RETAIL, "small office": SpaceTypology.BUSINESS_OFFICE,
    "clinic waiting room": SpaceTypology.BUSINESS_OFFICE, "hotel lobby": SpaceTypology.HOSPITALITY_LOUNGE,
}
HELDOUT_START = 100_000
"""Held-out generated rooms come from indices no training room uses."""
HELDOUT_PREFIX = "synthetic-heldout-"


def make_room(index: int) -> Window:
    graph, scenario, shop = generate(index)
    prefix = HELDOUT_PREFIX if index >= HELDOUT_START else "synthetic-"
    room_name = f"{prefix}{index:06d}"
    return Window(window_id=f"{room_name}:whole", scan_id=str(graph.scan_id), centre=None, graph=graph,
                  scenario=scenario, route="scan", space_typology=SPACE_TYPES[shop.name].value)


def run_rooms(run: pathlib.Path, count: int, heldout: int = 0) -> None:
    output = run / "windows.jsonl"
    done = {row["window_id"] for row in _rows(output)}
    for index in [*range(count), *range(HELDOUT_START, HELDOUT_START + heldout)]:
        window = make_room(index)
        if window.window_id in done:
            continue
        _append(output, window.as_dict())
        _write_json(run / "progress.json", {"stage": "rooms", "done": index + 1, "total": count})
        print(f"room {index + 1}/{count}", flush=True)


def _scramble_task(row: dict) -> list[dict]:
    window = Window.from_dict(row)
    checker = checker_for(window)
    made = scramble(window.graph, checker, 3, seed=zlib.crc32(window.window_id.encode()), how=LIGHT)
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


def _capped(rows: list[dict], windows: dict[str, Window], cap: int | None) -> list[dict]:
    """At most `cap` rows, taken round-robin across space types so no one kind of room dominates."""
    if cap is None or len(rows) <= cap:
        return rows
    by_type: dict[str, list[dict]] = {}
    for row in sorted(rows, key=lambda row: zlib.crc32(row["variant"].encode())):
        by_type.setdefault(windows[row["window"]].space_typology or "", []).append(row)
    picked, queues = [], list(by_type.values())
    while len(picked) < cap and any(queues):
        for queue in queues:
            if queue and len(picked) < cap:
                picked.append(queue.pop(0))
    return picked


def _real_heldout(real: pathlib.Path) -> tuple[list[dict], list[dict], list[dict]]:
    """The real held-out variants with prompts rebuilt in the current format, their windows and variants."""
    ids = {row["variant"] for row in _rows(real / "dataset/heldout.jsonl")}
    variants = [row for row in _rows(real / "variants.jsonl") if row["variant_id"] in ids]
    window_ids = {variant["window_id"] for variant in variants}
    window_rows = [row for row in _rows(real / "windows.jsonl") if row["window_id"] in window_ids]
    windows = {row["window_id"]: Window.from_dict(row) for row in window_rows}
    prompts = [_prompt_row(variant, windows[variant["window_id"]], None) for variant in variants]
    return prompts, window_rows, variants


EXTRA_HELDOUT = "extra_heldout.jsonl"
"""Optional rows of {"window": ..., "variant": ...} evaluated with the held-out set, such as rooms in the web app."""


def _extra_heldout(run: pathlib.Path) -> tuple[list[dict], list[dict], list[dict]]:
    rows = _rows(run / EXTRA_HELDOUT)
    window_rows = [row["window"] for row in rows]
    variants = [row["variant"] for row in rows]
    windows = {row["window_id"]: Window.from_dict(row) for row in window_rows}
    prompts = [_prompt_row(variant, windows[variant["window_id"]], None) for variant in variants]
    return prompts, window_rows, variants


def run_dataset(run: pathlib.Path, real: pathlib.Path, max_sft: int | None = None) -> None:
    if (run / "report.json").exists():
        return
    synthetic_windows = _rows(run / "windows.jsonl")
    synthetic_variants = [row for row in _rows(run / "variants.jsonl") if row["variant_id"]]
    targets = {row["variant_id"]: row for row in _rows(run / "targets.jsonl")}
    real_heldout, real_windows, real_variants = _real_heldout(real)
    extra_heldout, extra_windows, extra_variants = _extra_heldout(run)
    real_heldout, real_windows, real_variants = (real_heldout + extra_heldout, real_windows + extra_windows,
                                                 real_variants + extra_variants)
    windows = {row["window_id"]: Window.from_dict(row) for row in synthetic_windows}
    sft, rl, synthetic_heldout = [], [], []
    for variant in synthetic_variants:
        row = _prompt_row(variant, windows[variant["window_id"]], targets.get(variant["variant_id"]))
        if variant["window_id"].startswith(HELDOUT_PREFIX):
            synthetic_heldout.append({key: value for key, value in row.items() if key != "target"})
            continue
        rl.append({key: value for key, value in row.items() if key != "target"})
        if "target" in row:
            sft.append({"messages": [*row["messages"], {"role": "assistant", "content": row["target"]}],
                        "variant": row["variant"], "window": row["window"]})
    sft = _capped(sft, windows, max_sft)
    heldout = [*real_heldout, *synthetic_heldout]
    dataset = run / "dataset"
    dataset.mkdir(exist_ok=True)
    for name, rows in (("sft", sft), ("rl", rl), ("heldout", heldout)):
        (dataset / f"{name}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    for name, synthetic, real_rows in (("windows", synthetic_windows, real_windows),
                                       ("variants", _rows(run / "variants.jsonl"), real_variants)):
        (run / f"{name}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in [*synthetic, *real_rows]))
    real_report = json.loads((real / "report.json").read_text())
    _write_json(run / "report.json", {"windows": [{"window_id": row["window_id"], "scan": "synthetic"}
                                                  for row in synthetic_windows] +
                                                 [row for row in real_report["windows"] if row["window_id"]
                                                  in {window["window_id"] for window in real_windows}],
                                        "split": {"totals": {"train": len(rl), "heldout_real": len(real_heldout),
                                                             "heldout_synthetic": len(synthetic_heldout)},
                                                  "by_scan": {}, "held_out": real_report["split"]["held_out"]},
                                        "variants": {"total": len(synthetic_variants) + len(real_variants)},
                                        "targets": target_quality(list(targets.values()))})
    _write_json(run / "progress.json", {"stage": "dataset", "done": True, "rooms": len(synthetic_windows),
                                         "sft": len(sft), "rl": len(rl), "heldout_real": len(real_heldout),
                                         "heldout_synthetic": len(synthetic_heldout)})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("rooms", "scrambles", "targets", "dataset", "corrections"))
    parser.add_argument("--run", type=pathlib.Path, required=True)
    parser.add_argument("--real", type=pathlib.Path)
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--heldout", type=int, default=0, help="generated rooms kept out of training")
    parser.add_argument("--max-sft", type=int, default=None)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    args.run.mkdir(parents=True, exist_ok=True)
    if args.stage == "rooms":
        run_rooms(args.run, args.count, args.heldout)
    elif args.stage == "scrambles":
        run_scrambles(args.run, args.workers)
    elif args.stage == "targets":
        run_targets(args.run, args.workers, Progress(args.run / "target_progress.json"))
    elif args.stage == "corrections":
        run_corrections(args.run, Progress(args.run / "corrections_progress.json"), args.workers)
    else:
        run_dataset(args.run, args.real, args.max_sft)


if __name__ == "__main__":
    main()
