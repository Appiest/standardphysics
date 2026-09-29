"""Commands that score the checks: the dataset, the sweep, Weave and the experiment grid."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..evaluation import (
    DEFAULT_GRID,
    DEFAULT_SETUPS,
    blocker,
    evaluate,
    evaluate_in_weave,
    log_experiments,
    previewing,
    run_accessibility_sweep,
    run_grid,
    save,
    save_accessibility_sweep,
    save_experiments,
    target,
)
from ..evaluation import dataset as labelled_cases
from ..evaluation.scorers import LOWER_IS_BETTER, SCORERS
from ..rules import load_ledger, load_pack
from ..tracing import is_live, project_url
from .common import (
    measurements,
    nothing_enabled,
    router_for,
)

WEAVE_NOT_CONFIGURED = (
    "Weave is not configured. Set WANDB_PROJECT and WANDB_ENTITY, then try again."
)


GRID_STAYED_LOCAL = "The grid is on disk. To put it where ARIA reads it: "


GRID_HEADER = (
    f"{'configuration':34} {'weakest score':28} {'error in':>9} "
    f"{'measurements':>13} {'seconds':>8}"
)


DEFAULT_EVALUATION_PATH = "runs/evaluation.json"


DEFAULT_GRID_PATH = "runs/experiments.json"


def evaluate_checks(args) -> int:
    pack, ledger = load_pack(), load_ledger()
    if nothing_enabled(pack, ledger):
        return 1
    result = evaluate(
        measure=measurements(args.provider),
        rules=pack,
        ledger=ledger,
        router=router_for(args.router),
        run_fixes=not args.no_fixes,
        version=args.version,
    )
    print(f"rule pack {result.rulepack_version}, {len(result.outcomes)} cases")
    print(f"completed: {result.completed}")
    for name, value in sorted(result.scores.items()):
        direction = "lower is better" if name in LOWER_IS_BETTER else ""
        print(f"  {name:24} {value:.4f}  {direction}")
    for case_id in result.failures:
        print(f"  failed: {case_id}", file=sys.stderr)
    written = save(result, Path(args.out))
    print(f"per-case results: {written}")
    if result.weave_url:
        print(f"traces: {result.weave_url}")
    if result.evaluation_url:
        print(f"evaluation in weave: {result.evaluation_url}")
    return 0 if result.completed else 1


def sweep_accessibility(args) -> int:
    result = run_accessibility_sweep(
        evaluations=args.evaluations,
        seed=args.seed,
        record_runs=args.record_runs,
    )
    written = save_accessibility_sweep(result, Path(args.out))
    print(
        f"{result.evaluations} evaluations across "
        f"{result.layouts} layouts and {result.routes} routes"
    )
    print(f"failures: {result.failures}")
    for profile_id, count in result.profile_route_fits.items():
        total = result.profile_evaluations[profile_id]
        print(f"  {profile_id:16} {count}/{total} routes fit")
    print(f"seed: {result.seed}")
    print(f"digest: {result.digest}")
    print(f"result: {written}")
    return 0 if result.passed else 1


def score_in_weave(args) -> int:
    """Every configuration against every case, left in Weave's Evals tab."""
    if not is_live():
        print(WEAVE_NOT_CONFIGURED, file=sys.stderr)
        return 1
    if not args.preview_unverified and nothing_enabled(load_pack(), load_ledger()):
        return 1
    setups = previewing(DEFAULT_SETUPS) if args.preview_unverified else DEFAULT_SETUPS
    cases = labelled_cases()[: args.cases] if args.cases else None
    for label, result in evaluate_in_weave(setups, cases=cases, provenance={"commit": _commit()}).items():
        print(f"\n{label}")
        _print_weave_scores(result)
    print(f"\nevals: {project_url()}")
    return 0


def _commit() -> str:
    """The commit a Weave run scored: the image's baked-in SP_GIT_SHA, else the checkout's HEAD."""
    baked = os.environ.get("SP_GIT_SHA")
    if baked:
        return baked
    found = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return found.stdout.strip() or "unknown"


def _print_weave_scores(result: dict) -> None:
    for name in sorted(SCORERS):
        mean = _mean_of(result.get(name))
        direction = "lower is better" if name in LOWER_IS_BETTER else ""
        reading = f"{mean:.4f}" if mean is not None else "nothing to score"
        print(f"  {name:24} {reading:>16}  {direction}")


def _mean_of(scored) -> float | None:
    """Weave summarizes a numeric scorer as {"mean": value}."""
    if isinstance(scored, dict):
        return scored.get("mean")
    return scored if isinstance(scored, (int, float)) else None


def run_experiments(args) -> int:
    """The grid as W&B runs, which is the form ARIA reads."""
    if not args.preview_unverified and nothing_enabled(load_pack(), load_ledger()):
        return 1
    setups = previewing(DEFAULT_GRID) if args.preview_unverified else list(DEFAULT_GRID)
    if args.dry_run:
        for configuration in setups:
            print(f"  {configuration.label}")
        return 0
    cases = labelled_cases()[: args.cases] if args.cases else None
    experiments = run_grid(setups, cases=cases)
    _print_grid(experiments)
    print(f"\nthe grid: {save_experiments(experiments, Path(args.out))}")
    _print_runs(experiments)
    return 0 if all(e.result.completed for e in experiments) else 1


def _print_grid(experiments) -> None:
    print(f"\n{GRID_HEADER}")
    for experiment in experiments:
        metrics = experiment.metrics()
        print(
            f"{experiment.setup.label:34} {_weakest(metrics):28} "
            f"{_reading(metrics.get('measurement_error_in')):>9} "
            f"{metrics['cost/measurements_taken']:>13} "
            f"{metrics['cost/wall_seconds']:>8.1f}"
        )


def _weakest(metrics: dict) -> str:
    """The lowest of the scores where higher is better, and which one it is."""
    scored = {
        name: value
        for name, value in metrics.items()
        if name in SCORERS and name not in LOWER_IS_BETTER
    }
    if not scored:
        return "nothing to score"
    name = min(scored, key=lambda key: scored[key])
    return f"{scored[name]:.4f} {name}"


def _reading(value: float | None) -> str:
    return f"{value:.4f}" if value is not None else "-"


def _print_runs(experiments) -> None:
    reason = blocker()
    if reason is not None:
        print(f"{GRID_STAYED_LOCAL}{reason}", file=sys.stderr)
        return
    where = target()
    assert where is not None
    project, team = where
    print(f"\n{len(experiments)} runs in {team or 'your default entity'}/{project}")
    for url in log_experiments(experiments):
        print(f"  {url}")
