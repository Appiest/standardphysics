"""Collect every fine-tuning run's numbers into one ledger for notebooks/finetune_story.py.

Runs on compute-box, where the training harness keeps its run folders, and reads
only what the runs wrote: progress files, RL metric logs, dataset reports and the
Fireworks model list. Every number in the ledger names the file it came from.

    ssh compute-box 'cd ~/sp-finetune && set -a && . ./fireworks.env && set +a && \\
        venv/bin/python -' < scripts/finetune_ledger.py > notebooks/public/finetune_ledger.json
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import urllib.request
from dataclasses import dataclass, field

ROOT = pathlib.Path(os.environ.get("SP_FINETUNE_ROOT", pathlib.Path.home() / "sp-finetune"))
ACCOUNT = "amelia-team"
BASE_MODEL = "qwen3p8-27b"
RUN_ID = re.compile(r"run-[0-9a-f]{32}")


@dataclass(frozen=True)
class Run:
    key: str
    title: str
    question: str
    eval_set: str
    progress: str | None = None
    rl_metrics: str | None = None
    extras: dict = field(default_factory=dict)


RUNS = (
    Run(
        key="room6",
        title="One room",
        question="Can the model learn to fix one real shop from 28 worked answers?",
        eval_set="10 held-out versions of the same room, four tries each",
        rl_metrics="ft-runs/room6/qwen3p8-27b/rl_metrics.jsonl",
        extras={"log": "ft-runs/room6/orchestrator.log", "data": "ft-runs/room6/data"},
    ),
    Run(
        key="multiroom",
        title="Many real rooms",
        question="Does it generalise when the training rooms come from six real scans?",
        eval_set="65 held-out variants from two buildings, four tries each",
        progress="ft-runs/multiroom/v2/PROGRESS_MULTIROOM.json",
        rl_metrics="repo/runs/finetune/multiroom/v2/qwen3p8-27b/rl_metrics.jsonl",
        extras={"report": "ft-runs/multiroom/v2/report.json"},
    ),
    Run(
        key="synthetic",
        title="500 generated shops",
        question="Do 1,498 answers on generated shops help on real ones?",
        eval_set="The same 65 real held-out variants, four tries each",
        progress="ft-runs/synthetic-20260924/PROGRESS_SYNTHETIC.json",
        rl_metrics="synthetic/runs/finetune/synthetic/qwen3p8-27b/rl_metrics.jsonl",
        extras={"report": "ft-runs/synthetic-20260924/report.json"},
    ),
    Run(
        key="harness",
        title="Real and generated together",
        question="What happens when real scans and generated shops share one dataset?",
        eval_set="120 held-out variants, 67 real and 53 generated",
        progress="ft-runs/harness/PROGRESS_HARNESS_TRAINING.json",
        rl_metrics="ft-runs/harness/qwen3p8-27b/rl_metrics.jsonl",
        extras={"report": "ft-runs/harness/report.json"},
    ),
    Run(
        key="ablation-a",
        title="RL on real rooms only",
        question="Starting from the same SFT, does RL on real rooms alone beat a half-and-half mix?",
        eval_set="Real held-out variants after RL steps 4 and 8",
        progress="ft-runs/ablation/arm-A/PROGRESS.json",
        rl_metrics="ft-runs/ablation/arm-A/rl_metrics.jsonl",
    ),
    Run(
        key="ablation-b",
        title="RL on a half-and-half mix",
        question="The same test, with half of each RL step drawn from generated shops",
        eval_set="Real held-out variants after RL steps 4 and 8",
        progress="ft-runs/ablation/arm-B/PROGRESS.json",
        rl_metrics="ft-runs/ablation/arm-B/rl_metrics.jsonl",
    ),
    Run(
        key="arkit",
        title="Apple ARKit layouts",
        question="Does adding 388 layouts converted from Apple's ARKitScenes help?",
        eval_set="163 held-out variants across real, generated and ARKit rooms",
        progress="harness/runs/finetune/arkit/PROGRESS_TRAINING.json",
        extras={"dataset": "harness/runs/finetune/arkit/dataset_report.json"},
    ),
    Run(
        key="overnight",
        title="Solver answers",
        question="Can the model learn from the answers our room solver found?",
        eval_set="65 real held-out variants, one try each",
        progress="overnight/out/sft-1.progress.json",
        extras={"night": "overnight/out/PROGRESS.json"},
    ),
    Run(
        key="menu",
        title="Pick from a menu",
        question="If the model chooses from pre-checked moves, does training still help?",
        eval_set="52 held-out rooms, choosing blind from a menu of legal moves",
        progress="tonight-0927/progress.json",
        rl_metrics="tonight-0927/run/rl_metrics.jsonl",
    ),
)

EVAL_LABELS = {"eval_base": "Base", "eval_sft": "After SFT", "eval_rl": "After RL"}


def read_json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text())


def read_rows(relative: str) -> list[dict]:
    return [json.loads(line) for line in (ROOT / relative).read_text().splitlines() if line.strip()]


def count_lines(relative: str) -> int:
    return sum(1 for line in (ROOT / relative).read_text().splitlines() if line.strip())


def eval_label(step_name: str) -> str:
    if step_name in EVAL_LABELS:
        return EVAL_LABELS[step_name]
    return "After RL step " + step_name.rsplit("step", 1)[-1]


def evaluation(label: str, summary: dict) -> dict:
    return {
        "label": label,
        "samples": summary["samples"],
        "cleared": summary.get("all_fixable_cleared_rate"),
        "accepted": summary.get("gate_acceptance"),
        "rules_pass": summary.get("hard_constraint_pass_rate"),
        "reward": summary.get("mean_reward"),
    }


def progress_evaluations(steps: dict) -> list[dict]:
    return [
        evaluation(eval_label(name), step["summary"])
        for name, step in steps.items()
        if name.startswith("eval_") and step.get("status") == "done" and step.get("summary")
    ]


def progress_training(progress: dict) -> dict:
    steps, plan = progress["steps"], progress["plan"]
    sft, rl = steps.get("sft", {}), steps.get("rl", {})
    return {
        "sft_rows": sft.get("rows"),
        "sft_epochs": plan.get("sft_epochs") if sft else None,
        "sft_optimizer_steps": sft.get("optimizer_steps"),
        "rl_steps_done": rl.get("completed_steps"),
        "rl_steps_planned": plan.get("rl_steps") if rl else None,
        "rl_rollouts_per_step": plan.get("rl_prompts_per_step", 0) * plan.get("rl_group_size", 0) if rl else None,
        "rl_status": rl.get("status"),
        "rl_updated_at": rl.get("updated_at"),
        "lora_rank": plan.get("lora_rank"),
        "budget_dollars": plan.get("budget_dollars"),
    }


def promoted_models(steps: dict) -> list[str]:
    return [step["model"].rsplit("/", 1)[-1] for name, step in steps.items() if name.startswith("promote_") and "model" in step]


def sessions(progress: dict) -> list[dict]:
    return [
        {"run_id": job["run_id"], "from_state": job.get("from_state"), "opened_at": job["opened_at"]}
        for job in progress["jobs"]
    ]


def from_progress(run: Run) -> dict:
    progress = read_json(run.progress)
    return {
        "training": progress_training(progress),
        "evaluations": progress_evaluations(progress["steps"]),
        "models": promoted_models(progress["steps"]),
        "sessions": sessions(progress),
        "spend": progress.get("spend"),
        "sources": [run.progress],
    }


def room6_log_evaluations(log: str) -> list[dict]:
    found = re.findall(r"^eval (base|sft|rl): (\{.*\})$", (ROOT / log).read_text(), flags=re.MULTILINE)
    return [evaluation(EVAL_LABELS["eval_" + stage], json.loads(summary.replace("'", '"'))) for stage, summary in found]


def room6_log_sessions(log: str) -> list[dict]:
    text = (ROOT / log).read_text()
    started = re.search(r"^(\S+Z) training attempt 1$", text, flags=re.MULTILINE).group(1)
    return [{"run_id": run_id, "from_state": None, "opened_at": started} for run_id in dict.fromkeys(RUN_ID.findall(text))]


def room6_log_plan(log: str) -> dict:
    """The orchestrator printed its preflight estimate, with epochs and steps multiplied out, before training."""
    return json.loads(re.search(r'^(\{"sft_rows".*\})$', (ROOT / log).read_text(), flags=re.MULTILINE).group(1))


def from_room6(run: Run) -> dict:
    log, data = run.extras["log"], run.extras["data"]
    curve = read_rows(run.rl_metrics)
    sft_rows, plan = count_lines(f"{data}/sft.jsonl"), room6_log_plan(log)
    return {
        "training": {
            "sft_rows": sft_rows,
            "sft_epochs": plan["sft_rows"] // sft_rows,
            "rl_steps_done": len(curve),
            "rl_steps_planned": plan["rl_rollouts"] // curve[0]["samples"],
            "rl_rollouts_per_step": curve[0]["samples"],
            "rl_status": "done",
        },
        "evaluations": room6_log_evaluations(log),
        "models": ["room6-qwen3p8-27b-sft", "room6-qwen3p8-27b-rl"],
        "sessions": room6_log_sessions(log),
        "spend": curve[-1]["spend"],
        "funnel": [
            {"count": count_lines(f"{data}/variants.jsonl"), "label": "versions of one room"},
            {"count": count_lines(f"{data}/sft.jsonl"), "label": "worked answers for SFT"},
            {"count": count_lines(f"{data}/rl.jsonl"), "label": "RL prompts"},
            {"count": count_lines(f"{data}/heldout.jsonl"), "label": "held out"},
        ],
        "sources": [log, f"{data}/*.jsonl", run.rl_metrics],
    }


def report_funnel(report_path: str, sft_rows: int | None) -> list[dict]:
    report = read_json(report_path)
    targets = report["targets"].get("all", report["targets"])
    return [
        {"count": len(report["windows"]), "label": "rooms"},
        {"count": report["variants"]["total"], "label": "scrambled versions"},
        {"count": targets["with_target"], "label": "with a found fix"},
        {"count": sft_rows, "label": "SFT rows after fitting"},
    ]


def arkit_funnel(dataset_path: str) -> list[dict]:
    report = read_json(dataset_path)
    before = report["sft_before_fit"]
    return [
        {"count": before["arkit"], "label": "ARKit layouts"},
        {"count": before["generated"], "label": "generated shops"},
        {"count": before["real_scans"], "label": "real scan windows"},
        {"count": report["fit"]["sft"]["kept"], "label": "SFT rows after fitting"},
    ]


def cleared_fraction(text: str) -> dict:
    cleared, total = (int(part) for part in re.match(r"(\d+)/(\d+)", text).groups())
    return {"cleared": cleared, "of": total}


def five_loop(night_path: str) -> dict:
    """The 2026-09-26 night graded answers inside a five-try propose-check-revise loop, not single shots."""
    runs = {name: value if isinstance(value, str) else value.get("cleared", "") for name, value in read_json(night_path)["runs"].items()}
    return {
        "real_heldout": {
            "Base model alone": cleared_fraction(runs["base-A-heldout"]),
            "After SFT, alone": cleared_fraction(runs["sft-A-heldout"]),
            "Local 1-layer LoRA, alone": cleared_fraction(runs["local-lora-A-heldout"]),
            "Solver alone, no model": cleared_fraction(runs["solver-only-heldout"]),
            "Base model with the solver": cleared_fraction(runs["base-B-heldout"]),
        },
        "test_shops": {
            "Base model alone": cleared_fraction(runs["base-A-test"]),
            "Base model with the solver": cleared_fraction(runs["base-B2-test"]),
        },
    }


def overnight_funnel(night_path: str) -> list[dict]:
    mix = read_json(night_path)["runs"]["final mix"]
    return [
        {"count": mix["real_solver"], "label": "solver answers on real rooms"},
        {"count": mix["correction"], "label": "corrections"},
        {"count": mix["synthetic"], "label": "generated shops"},
        {"count": mix["sft_rows"], "label": "SFT rows, real ones twice"},
    ]


def funnel_for(run: Run, sft_rows: int | None) -> list[dict] | None:
    if "report" in run.extras:
        return report_funnel(run.extras["report"], sft_rows)
    if "dataset" in run.extras:
        return arkit_funnel(run.extras["dataset"])
    if "night" in run.extras:
        return overnight_funnel(run.extras["night"])
    return None


def rl_curve(run: Run) -> list[dict]:
    if not run.rl_metrics or not (ROOT / run.rl_metrics).exists():
        return []
    return [
        {"step": row["step"], "reward": row["mean_reward"], "accepted": row["accepted"], "samples": row["samples"]}
        for row in read_rows(run.rl_metrics)
    ]


def collect_run(run: Run) -> dict:
    entry = from_room6(run) if run.key == "room6" else from_progress(run)
    entry.setdefault("funnel", funnel_for(run, entry["training"].get("sft_rows")))
    for extra in run.extras.values():
        if extra not in entry["sources"]:
            entry["sources"].append(extra)
    if run.rl_metrics and run.rl_metrics not in entry["sources"]:
        entry["sources"].append(run.rl_metrics)
    if "night" in run.extras:
        entry["five_loop"] = five_loop(run.extras["night"])
    return {
        "key": run.key,
        "title": run.title,
        "question": run.question,
        "eval_set": run.eval_set,
        "rl_curve": rl_curve(run),
        **entry,
    }


def link_parents(runs: list[dict]) -> None:
    """A run whose first session resumed another run's saved state is drawn branching off that run."""
    owner = {session["run_id"]: run["key"] for run in runs for session in run["sessions"]}
    for run in runs:
        first = run["sessions"][0].get("from_state") or ""
        match = RUN_ID.search(first)
        run["parent"] = owner.get(match.group(0)) if match else None


def fireworks_adapters() -> list[dict]:
    request = urllib.request.Request(
        f"https://api.fireworks.ai/v1/accounts/{ACCOUNT}/models?pageSize=200",
        headers={"Authorization": f"Bearer {os.environ['FIREWORKS_API_KEY']}"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        models = json.load(response)["models"]
    return sorted(
        (
            {"name": model["name"].rsplit("/", 1)[-1], "created": model["createTime"], "rank": model["peftDetails"]["r"]}
            for model in models
            if BASE_MODEL in (model.get("peftDetails") or {}).get("baseModel", "")
        ),
        key=lambda adapter: adapter["created"],
    )


def main() -> None:
    runs = [collect_run(run) for run in RUNS]
    link_parents(runs)
    ledger = {
        "collected_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "base_model": "Qwen 3.8 27B",
        "trainer": "Fireworks serverless LoRA training",
        "adapters": fireworks_adapters(),
        "runs": runs,
    }
    print(json.dumps(ledger, indent=1))


if __name__ == "__main__":
    main()
