"""The SFT dataset built around real ARKitScenes rooms, with every earlier real and generated source kept.

    windows      the ARKitScenes windows, one room in `HELDOUT_EVERY` held out by a hash of its id
    scrambles    `synthetic_data.run_scrambles`; targets: `multiroom_data.run_targets` (put back, else search)
    dataset      SFT rows from the ARKitScenes training rooms, the harness run's generated rooms, and the
                 real-scan windows of multiroom v2 (prompts rebuilt in the current format); held out: the
                 ARKitScenes held-out rooms, the 67 real held-out rooms and the generated held-out rooms.
                 Rows are then held to the prompt length cap by `fit_dataset`.

    python scripts/finetune/arkit_dataset.py windows --arkit ~/sp-data/arkit --run runs/finetune/arkit
    python scripts/finetune/arkit_dataset.py dataset --run runs/finetune/arkit --harness runs/finetune/harness \\
        --real ~/sp-finetune/repo/runs/finetune/multiroom/v2
"""

from __future__ import annotations

import argparse
import json
import pathlib
import zlib

from fit_dataset import fit
from multiroom_data import _prompt_row, target_quality
from standardphysics_agents.training.windows import Window
from synthetic_data import HELDOUT_PREFIX

HELDOUT_EVERY = 10
ARKIT_HELDOUT = "arkit-heldout-"


def _rows(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def _write(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def held_out(window_id: str) -> bool:
    return zlib.crc32(window_id.encode()) % HELDOUT_EVERY == 0


def windows(arkit: pathlib.Path, run: pathlib.Path) -> dict:
    rows = []
    for row in _rows(arkit / "windows.jsonl"):
        if held_out(row["window_id"]):
            row = {**row, "window_id": row["window_id"].replace("arkit-", ARKIT_HELDOUT, 1)}
        rows.append(row)
    _write(run / "windows.jsonl", rows)
    return {"rooms": len(rows), "held_out": sum(1 for row in rows if row["window_id"].startswith(ARKIT_HELDOUT))}


def _sft_row(row: dict) -> dict:
    return {"messages": [*row["messages"], {"role": "assistant", "content": row["target"]}],
            "variant": row["variant"], "window": row["window"]}


def _arkit_rows(run: pathlib.Path) -> tuple[list[dict], list[dict]]:
    windows_by_id = {row["window_id"]: Window.from_dict(row) for row in _rows(run / "windows.jsonl")}
    targets = {row["variant_id"]: row for row in _rows(run / "targets.jsonl")}
    sft, heldout = [], []
    for variant in (row for row in _rows(run / "variants.jsonl") if row.get("variant_id")):
        row = _prompt_row(variant, windows_by_id[variant["window_id"]], targets.get(variant["variant_id"]))
        if variant["window_id"].startswith(ARKIT_HELDOUT):
            heldout.append({key: value for key, value in row.items() if key != "target"})
        elif "target" in row:
            sft.append(_sft_row(row))
    return sft, heldout


def _real_sft(real: pathlib.Path) -> tuple[list[dict], list[dict], list[dict]]:
    """The real-scan SFT rows of multiroom v2, prompts rebuilt, with their windows and variants."""
    old = {row["variant"]: row for row in _rows(real / "dataset" / "sft.jsonl")}
    variants = [row for row in _rows(real / "variants.jsonl") if row.get("variant_id") in old]
    window_rows = [row for row in _rows(real / "windows.jsonl")
                   if row["window_id"] in {variant["window_id"] for variant in variants}]
    by_id = {row["window_id"]: Window.from_dict(row) for row in window_rows}
    rows = []
    for variant in variants:
        prompt = _prompt_row(variant, by_id[variant["window_id"]], None)
        rows.append({**prompt, "messages": [*prompt["messages"], old[variant["variant_id"]]["messages"][-1]]})
    return rows, window_rows, variants


def dataset(run: pathlib.Path, harness: pathlib.Path, real: pathlib.Path) -> dict:
    arkit_sft, arkit_heldout = _arkit_rows(run)
    real_sft, real_windows, real_variants = _real_sft(real)
    harness_heldout = _rows(harness / "dataset" / "heldout.jsonl")
    sft = [*arkit_sft, *_rows(harness / "dataset" / "sft.jsonl"), *real_sft]
    heldout = [*arkit_heldout, *harness_heldout]
    arkit_windows, arkit_variants = _rows(run / "windows.jsonl"), _rows(run / "variants.jsonl")
    _write(run / "windows.jsonl", [*arkit_windows, *_rows(harness / "windows.jsonl"), *real_windows])
    _write(run / "variants.jsonl", [*arkit_variants, *_rows(harness / "variants.jsonl"), *real_variants])
    _write(run / "dataset" / "sft.jsonl", sft)
    _write(run / "dataset" / "rl.jsonl", [])
    _write(run / "dataset" / "heldout.jsonl", heldout)
    fitted = fit(run / "dataset")
    kinds = {"arkit": len(arkit_sft), "generated": len(_rows(harness / "dataset" / "sft.jsonl")), "real_scans": len(real_sft)}
    heldout_kinds = {"arkit": len(arkit_heldout),
                     "generated": sum(1 for row in harness_heldout if row["variant"].startswith(HELDOUT_PREFIX)),
                     "real_scans": sum(1 for row in harness_heldout if not row["variant"].startswith(HELDOUT_PREFIX))}
    report = {"sft_before_fit": kinds, "heldout_before_fit": heldout_kinds, "fit": fitted}
    (run / "dataset_report.json").write_text(json.dumps(report, indent=2) + "\n")
    write_trainer_report(run)
    return report


def write_trainer_report(run: pathlib.Path) -> None:
    """The `report.json` the trainer reads for its data composition."""
    windows_rows = _rows(run / "windows.jsonl")
    held = [row["window_id"] for row in windows_rows if row["window_id"].startswith(ARKIT_HELDOUT)]
    summary = {"windows": [{"window_id": row["window_id"], "scan": row.get("source", "")} for row in windows_rows],
               "split": {"totals": {"arkit_heldout_rooms": len(held)}, "by_scan": {}, "held_out": {"arkit": held}},
               "variants": {"total": sum(1 for row in _rows(run / "variants.jsonl") if row.get("variant_id"))},
               "targets": target_quality(_rows(run / "targets.jsonl"))}
    (run / "report.json").write_text(json.dumps(summary) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("windows", "dataset", "report"))
    parser.add_argument("--run", type=pathlib.Path, required=True)
    parser.add_argument("--arkit", type=pathlib.Path)
    parser.add_argument("--harness", type=pathlib.Path)
    parser.add_argument("--real", type=pathlib.Path)
    args = parser.parse_args()
    if args.stage == "windows":
        print(json.dumps(windows(args.arkit, args.run), indent=2))
    elif args.stage == "report":
        write_trainer_report(args.run)
    else:
        print(json.dumps(dataset(args.run, args.harness, args.real), indent=2))


if __name__ == "__main__":
    main()
