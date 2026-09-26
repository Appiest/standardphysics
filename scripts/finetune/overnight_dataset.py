"""Build the overnight training mix and the independent test set, with every target cleared by the checker.

    python scripts/finetune/overnight_dataset.py solve --data runs/finetune/multiroom/v2 --out RUN --workers 5
    python scripts/finetune/overnight_dataset.py train --real runs/finetune/multiroom/v2 \\
        --synthetic runs/finetune/synthetic-20260924 --trace five-loop-train.jsonl --out RUN --synthetic-rooms 400
    python scripts/finetune/overnight_dataset.py test --source runs/finetune/testshops

`solve` answers every real training-split room with `room_solver.solve` and appends one line per
room to RUN/solutions.jsonl, so it resumes. `train` writes a data directory `multiroom_train_data.load`
reads: SFT rows are real solver targets, correction rows (a refused first attempt from a base-model
five-loop trace, the checker's feedback, then the solver's clearing answer), and one cleared target
per synthetic shop. Every prompt is rendered with the current `prompt_messages`. `test` keeps one
variant per synthetic shop, so each test row is an independent shop.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import pathlib

from multiroom_data import _append, _rows, checker_for
from room_solver import solve
from standardphysics_agents.training import prompt_messages
from standardphysics_agents.training.windows import Window
from standardphysics_contracts import SceneGraph


def _prompt(variant: dict, window: Window) -> list[dict]:
    return prompt_messages(SceneGraph.model_validate(variant["graph"]), checker_for(window))


def _solve_task(task: tuple[dict, dict]) -> dict:
    variant, window_row = task
    window = Window.from_dict(window_row)
    solution, rejected = solve(SceneGraph.model_validate(variant["graph"]), checker_for(window))
    return {"variant": variant["variant_id"], "window": variant["window_id"],
            "target": solution.completion if solution and solution.clears else None,
            "verdict": solution.verdict if solution else None, "rejected": dict(rejected)}


def run_solve(data: pathlib.Path, out: pathlib.Path, workers: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    output = out / "solutions.jsonl"
    done = {row["variant"] for row in _rows(output)}
    train_ids = {row["variant"] for row in _rows(data / "dataset/rl.jsonl")}
    windows = {row["window_id"]: row for row in _rows(data / "windows.jsonl")}
    tasks = [(row, windows[row["window_id"]]) for row in _rows(data / "variants.jsonl")
             if row["variant_id"] in train_ids and row["variant_id"] not in done]
    with multiprocessing.get_context("spawn").Pool(workers) as pool:
        for result in pool.imap_unordered(_solve_task, tasks):
            _append(output, result)
            print(f"solved {result['variant']} clears={result['target'] is not None}", flush=True)


def _sft(messages: list[dict], target: str, variant: str, window: str, source: str) -> dict:
    return {"messages": [*messages, {"role": "assistant", "content": target}], "variant": variant,
            "window": window, "source": source}


def _correction(record: dict, prompt: list[dict], target: str) -> dict | None:
    """A refused first attempt, its feedback, then the answer that clears the unchanged room."""
    first = record["attempts"][0] if record.get("attempts") else None
    if first is None or first["accepted"]:
        return None
    feedback = {"role": "user", "content": json.dumps(first["feedback"], separators=(",", ":"))}
    messages = [*prompt, {"role": "assistant", "content": first["completion"]}, feedback]
    return _sft(messages, target, record["variant"], record["window_id"], "correction")


def _real_rows(real: pathlib.Path, out: pathlib.Path, trace: pathlib.Path | None) -> tuple[list, list, list]:
    windows = {row["window_id"]: Window.from_dict(row) for row in _rows(real / "windows.jsonl")}
    variants = {row["variant_id"]: row for row in _rows(real / "variants.jsonl") if row["variant_id"]}
    solutions = {row["variant"]: row for row in _rows(out / "solutions.jsonl")}
    traces = {row["variant"]: row for row in _rows(trace)} if trace else {}
    sft, rl, heldout = [], [], []
    for row in _rows(real / "dataset/rl.jsonl"):
        variant = variants[row["variant"]]
        prompt = _prompt(variant, windows[variant["window_id"]])
        rl.append({"messages": prompt, "variant": row["variant"], "window": variant["window_id"]})
        target = (solutions.get(row["variant"]) or {}).get("target")
        if target:
            sft.append(_sft(prompt, target, row["variant"], variant["window_id"], "real-solver"))
            correction = _correction(traces[row["variant"]], prompt, target) if row["variant"] in traces else None
            if correction:
                sft.append(correction)
    for row in _rows(real / "dataset/heldout.jsonl"):
        variant = variants[row["variant"]]
        heldout.append({"messages": _prompt(variant, windows[variant["window_id"]]), "variant": row["variant"],
                        "window": variant["window_id"]})
    return sft, rl, heldout


def _synthetic_rows(synthetic: pathlib.Path, rooms: int) -> tuple[list, list, list]:
    windows = {row["window_id"]: row for row in _rows(synthetic / "windows.jsonl")
               if row["window_id"].startswith("synthetic-")}
    targets = {row["variant_id"]: row for row in _rows(synthetic / "targets.jsonl")}
    chosen, sft = {}, []
    for variant in _rows(synthetic / "variants.jsonl"):
        target = targets.get(variant.get("variant_id") or "")
        clears = target and (target.get("verdict") or {}).get("gate_accepts") and target["verdict"]["fixable_left"] == 0
        if not clears or variant["window_id"] in chosen or variant["window_id"] not in windows or len(chosen) >= rooms:
            continue
        chosen[variant["window_id"]] = variant
        prompt = _prompt(variant, Window.from_dict(windows[variant["window_id"]]))
        sft.append(_sft(prompt, target["target"], variant["variant_id"], variant["window_id"], "synthetic"))
    return sft, [windows[window_id] for window_id in chosen], list(chosen.values())


def _write(path: pathlib.Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def run_train(real: pathlib.Path, synthetic: pathlib.Path, trace: pathlib.Path | None, out: pathlib.Path,
              rooms: int) -> dict:
    real_sft, rl, heldout = _real_rows(real, out, trace)
    synthetic_sft, synthetic_windows, synthetic_variants = _synthetic_rows(synthetic, rooms)
    heldout_windows = {row["window"] for row in heldout}
    if any(row["window"] in heldout_windows for row in real_sft + rl):
        raise ValueError("a training row shares a window with the held-out set")
    (out / "dataset").mkdir(parents=True, exist_ok=True)
    _write(out / "dataset/sft.jsonl", real_sft + synthetic_sft)
    _write(out / "dataset/rl.jsonl", rl)
    _write(out / "dataset/heldout.jsonl", heldout)
    _write(out / "windows.jsonl", [*_rows(real / "windows.jsonl"), *synthetic_windows])
    _write(out / "variants.jsonl", [*_rows(real / "variants.jsonl"), *synthetic_variants])
    counts = {"real_solver": sum(r["source"] == "real-solver" for r in real_sft),
              "correction": sum(r["source"] == "correction" for r in real_sft),
              "synthetic": len(synthetic_sft), "rl_prompts": len(rl), "heldout": len(heldout)}
    (out / "composition.json").write_text(json.dumps(counts, indent=2))
    return counts


def run_test(source: pathlib.Path) -> dict:
    windows = {row["window_id"]: Window.from_dict(row) for row in _rows(source / "windows.jsonl")}
    picked: dict[str, dict] = {}
    for variant in _rows(source / "variants.jsonl"):
        if variant.get("variant_id") and variant["window_id"] not in picked:
            picked[variant["window_id"]] = variant
    rows = [{"messages": _prompt(variant, windows[window_id]), "variant": variant["variant_id"], "window": window_id}
            for window_id, variant in picked.items()]
    (source / "dataset").mkdir(parents=True, exist_ok=True)
    _write(source / "dataset/heldout.jsonl", rows)
    for name in ("sft", "rl"):
        _write(source / f"dataset/{name}.jsonl", [])
    return {"shops": len(windows), "test_rows": len(rows)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("solve", "train", "test"))
    parser.add_argument("--data", type=pathlib.Path)
    parser.add_argument("--real", type=pathlib.Path)
    parser.add_argument("--synthetic", type=pathlib.Path)
    parser.add_argument("--trace", type=pathlib.Path)
    parser.add_argument("--source", type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--synthetic-rooms", type=int, default=400)
    args = parser.parse_args()
    if args.command == "solve":
        run_solve(args.data, args.out, args.workers)
    elif args.command == "train":
        print(json.dumps(run_train(args.real, args.synthetic, args.trace, args.out, args.synthetic_rooms)))
    else:
        print(json.dumps(run_test(args.source)))


if __name__ == "__main__":
    main()
