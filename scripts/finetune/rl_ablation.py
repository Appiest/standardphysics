"""Does RL on generated rooms hurt real rooms? Two RL arms from the same SFT state, checked on real rooms.

    A  every RL prompt is a real-scan window (the multiroom v2 training pool)
    B  every step takes half its prompts from that real pool and half from the generated rooms

Both arms start from the harness run's SFT state with identical settings, and both are evaluated on the
real held-out rooms after `EVAL_STEPS`, with the harness run's own SFT evaluation as step 0. Prompts are
rebuilt in the current format and held to the same 4,000 token cap as the harness run.

    python scripts/finetune/rl_ablation.py build --real-run ~/sp-finetune/repo/runs/finetune/multiroom/v2 \\
        --generated-run runs/finetune/harness --out runs/finetune/ablation
    python scripts/finetune/rl_ablation.py train --data runs/finetune/ablation --arm A --sft-state <state ref>
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import time
from dataclasses import asdict

import tinker
from fit_dataset import MAX_PROMPT_TOKENS, fit
from multiroom_data import _prompt_row
from multiroom_train_data import load
from progress import Progress, Spend
from serverless_train import BudgetExceeded, Plan, Trainer
from standardphysics_agents.training.windows import Window
from synthetic_data import HELDOUT_PREFIX

ARMS = {"A": (4, 0), "B": (2, 2)}
"""Prompts per step from the real pool and from the generated pool."""
EVAL_STEPS = (4, 8)
PLAN = {"rl_steps": 8, "rl_prompts_per_step": 4, "rl_group_size": 8, "eval_samples": 2, "budget_dollars": 9.5}


def _rows(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def _write(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _real_pool(real_run: pathlib.Path) -> tuple[list[dict], list[dict], list[dict]]:
    """The real training prompts rebuilt in the current format, with their windows and variants."""
    wanted = {row["variant"] for row in _rows(real_run / "dataset" / "rl.jsonl")}
    variants = [row for row in _rows(real_run / "variants.jsonl") if row.get("variant_id") in wanted]
    window_ids = {variant["window_id"] for variant in variants}
    window_rows = [row for row in _rows(real_run / "windows.jsonl") if row["window_id"] in window_ids]
    windows = {row["window_id"]: Window.from_dict(row) for row in window_rows}
    prompts = [{**_prompt_row(variant, windows[variant["window_id"]], None), "pool": "real"} for variant in variants]
    return prompts, window_rows, variants


def build(real_run: pathlib.Path, generated_run: pathlib.Path, out: pathlib.Path) -> dict:
    real, real_windows, real_variants = _real_pool(real_run)
    generated = [{**row, "pool": "generated"} for row in _rows(generated_run / "dataset" / "rl.jsonl")]
    heldout = [row for row in _rows(generated_run / "dataset" / "heldout.jsonl")
               if not row["variant"].startswith(HELDOUT_PREFIX)]
    _write(out / "windows.jsonl", [*_rows(generated_run / "windows.jsonl"), *real_windows])
    _write(out / "variants.jsonl", [*_rows(generated_run / "variants.jsonl"), *real_variants])
    _write(out / "dataset" / "rl.jsonl", [*real, *generated])
    _write(out / "dataset" / "heldout.jsonl", heldout)
    _write(out / "dataset" / "sft.jsonl", [])
    report = fit(out / "dataset")
    pools = {pool: sum(1 for row in _rows(out / "dataset" / "rl.jsonl") if row["pool"] == pool)
             for pool in ("real", "generated")}
    return {"fit": report, "rl_pools_after_fit": pools, "max_prompt_tokens": MAX_PROMPT_TOKENS}


def _by_room(rows: list[dict]) -> list[list[dict]]:
    rooms: dict[str, list[dict]] = {}
    for row in rows:
        rooms.setdefault(row["window"], []).append(row)
    return [rooms[key] for key in sorted(rooms)]


def pick(rows: list[dict], arm: str, step: int) -> list[dict]:
    """The step's prompts: a fixed count from each pool, one variant per room, cycling through rooms."""
    picked = []
    for pool, count in zip(("real", "generated"), ARMS[arm]):
        rooms = _by_room([row for row in rows if row["pool"] == pool])
        for index in range(count):
            variants = rooms[(step * count + index) % len(rooms)]
            picked.append(variants[(step * count + index) // len(rooms) % len(variants)])
    return picked


def estimate(plan: Plan, heldout: int, fitted: dict) -> float:
    """Every rollout priced at the longest RL prompt and every evaluation at the longest held-out prompt."""
    spend = Spend()
    rollouts = plan.rl_steps * plan.rl_prompts_per_step * plan.rl_group_size
    evals = len(EVAL_STEPS) * heldout * plan.eval_samples
    rl_tokens, eval_tokens = fitted["rl"]["longest_kept"], fitted["heldout"]["longest_kept"]
    spend.prefill_tokens += rollouts * rl_tokens + evals * eval_tokens
    spend.sample_tokens += (rollouts + evals) * plan.max_sample_tokens
    spend.train_tokens += rollouts * (rl_tokens + plan.max_sample_tokens)
    return spend.dollars


def _connect(trainer: Trainer, sft_state: str) -> int:
    rl = trainer.progress.get("rl")
    resume = rl.get("state_ref")
    trainer.connect(resume or sft_state, with_optimizer=bool(resume))
    return rl.get("completed_steps", 0) if resume else 0


def train(data_dir: pathlib.Path, arm: str, sft_state: str) -> None:
    plan = Plan(**{**asdict(Plan()), **PLAN, "sft_model_id": f"ablation-{arm}", "rl_model_id": f"ablation-{arm}"})
    run_dir = data_dir / f"arm-{arm}"
    run_dir.mkdir(parents=True, exist_ok=True)
    progress = Progress(run_dir / "PROGRESS.json")
    data = load(data_dir)
    fitted = json.loads((data_dir / "dataset" / "fit_report.json").read_text())
    ceiling = estimate(plan, len(data.heldout), fitted)
    progress.set("plan", {**asdict(plan), "arm": arm, "sft_state": sft_state, "pessimistic_dollars": round(ceiling, 2)})
    if ceiling > plan.budget_dollars:
        raise SystemExit(f"pessimistic estimate ${ceiling:.2f} is above ${plan.budget_dollars}; not launching")
    trainer = Trainer(plan, data, progress, run_dir, os.environ["FIREWORKS_API_KEY"])
    try:
        first = _connect(trainer, sft_state)
        progress.record("rl", status="running", started_at=progress.get("rl").get("started_at", time.time()))
        for step in range(first, plan.rl_steps):
            run_step(trainer, arm, step)
            if step + 1 in EVAL_STEPS:
                trainer.evaluate(f"{arm}-step{step + 1}")
        progress.record("rl", status="done", completed_steps=plan.rl_steps, finished_at=time.time())
    except BudgetExceeded as stop:
        progress.record("budget", status="stopped", reason=str(stop))
        raise SystemExit(3) from stop
    finally:
        trainer.close()


def run_step(trainer: Trainer, arm: str, step: int) -> None:
    picked = pick(trainer.data.rl, arm, step)
    snapshot = trainer.client.save_weights_for_sampler(f"rl-{step:04d}").result().path
    sampled = trainer.sample(snapshot, picked, trainer.plan.rl_group_size, trainer.plan.rl_temperature)
    datums, rewards = trainer.rl_datums(picked, sampled)
    if datums:
        trainer.charge(train=sum(datum.model_input.length for datum in datums))
        trainer.client.forward_backward(datums, "importance_sampling").result()
        trainer.client.optim_step(tinker.AdamParams(learning_rate=trainer.plan.rl_learning_rate,
                                                    beta1=0.9, beta2=0.95, eps=1e-12)).result()
    trainer.after_rl_step(step, rewards, len(datums))


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build")
    make.add_argument("--real-run", type=pathlib.Path, required=True)
    make.add_argument("--generated-run", type=pathlib.Path, required=True)
    make.add_argument("--out", type=pathlib.Path, required=True)
    run = commands.add_parser("train")
    run.add_argument("--data", type=pathlib.Path, required=True)
    run.add_argument("--arm", choices=sorted(ARMS), required=True)
    run.add_argument("--sft-state", required=True)
    args = parser.parse_args()
    if args.command == "build":
        print(json.dumps(build(args.real_run, args.generated_run, args.out), indent=2))
    else:
        train(args.data, args.arm, args.sft_state)


if __name__ == "__main__":
    main()
