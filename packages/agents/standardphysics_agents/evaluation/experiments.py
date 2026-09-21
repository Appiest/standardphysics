"""The evaluation grid, logged as W&B runs so ARIA can read it.

Weave's Evals tab compares configurations one against another, which is what
you want while you are reading them yourself. ARIA reads runs: a config of flat
hyperparameters, a summary of metrics, and a table behind each run. Given a
grid it will draw the heat map over two axes or the parallel coordinates over
all of them, and say which knob moved which metric. So the same cases and the
same scorers are logged both ways, and neither one re-implements a score.

The grid sweeps `cell_size`, the occupancy grid that every width and clearance
is measured on. Halving it quarters the cells and multiplies the measuring, and
coarsening it moves a measurement across a threshold and changes what the checks
report. One control run holds the cell size and swaps Lane B's pipeline for the
fixtures' stand-in, which says how much of the score rests on measuring the room
rather than on the rules.

The second axis is `fix_candidates`, how deep the fix agent's ladder goes
before it gives up. ARIA read an earlier grid and found that axis inert: 21 of
23 metrics identical across 4, 8 and 16 candidates, and the two that moved were
wall clocks at p = 0.88 and p = 0.74. It was right about the runs it had. The
run it proposed next is what overturned it — the three cell sizes in that grid
all happened to find every fix in the first four rungs, and at 20 mm and 30 mm
the ladder decides whether a third of the fixes are found at all. The axis
values were the unlucky part, not the axis. See `docs/aria_responses.md`.

The local JSON is the authoritative record here, as it is for a single run:
the grid runs and saves with no account, and W&B gets the same numbers when a
key is present.
"""

from __future__ import annotations

import functools
import itertools
import json
import logging
import os
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..rules import load_pack
from ..tracing import ENTITY_ENV, PROJECT_ENV
from .configuration import Setup, ledger, new_measurements, router, setup
from .dataset import Case, dataset
from .runner import EvaluationResult, evaluate
from .scorers import LOWER_IS_BETTER, SCORERS

KEY_ENV = "WANDB_API_KEY"

MODE_ENV = "WANDB_MODE"

GROUP = "shop-review-grid"

JOB_TYPE = "evaluation"

TAGS = ("shop-review", "evaluation", "aria")

GRID_AXES: dict[str, tuple[Any, ...]] = {
    "cell_size": (0.02, 0.025, 0.03, 0.035, 0.04),
    "fix_candidates": (4, 16),
}
"""Two axes, so the grid is the shape ARIA draws as a heat map.

The cell sizes step 5 mm either side of the 25 mm the pipeline ships. ARIA
proposed 35 mm as the next run, reasoning that 25 mm scored 1.000 and 50 mm was
four times faster for 0.965, so the boundary between them was unexplored. What
the sweep found is that there is no boundary to bracket: the scores flicker
with the resolution rather than falling off past a point.

The ladder is 4 against 16 because those are the two values whose outcomes
differ. Eight measures more candidates than four and scores the same as it
everywhere, so it would cost a run and say nothing."""

NOTES = (
    "One configuration of the shop review, scored against the labelled cases. "
    "cell_size is the occupancy grid every width is measured on, in metres. "
    "fix_candidates is how deep the fix agent searches. "
    f"{', '.join(sorted(LOWER_IS_BETTER))} is an error, so lower is better; "
    "every other score is a share of the cases, so higher is better."
)
"""What each run is, written for whoever or whatever reads it next."""


TABLE_COLUMNS = (
    "case", "description", "problems", "expected_problems", "questions",
    "action", "expected_action", "fix", "gate_accepted", "error",
    *sorted(SCORERS),
)

log = logging.getLogger(__name__)


class Counted:
    """A measurement provider that keeps a tally of what was asked of it.

    Wall seconds move with a warm cache, with the machine, and with whatever
    else is running on it. A count of measurements is the same number every
    time, which is what comparing one configuration against another needs. It
    counts questions asked rather than grids built, so a provider that answers
    from its cache still shows the work the configuration called for.
    """

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.calls: Counter[str] = Counter()

    def __getattr__(self, name: str) -> Any:
        answer = getattr(self.provider, name)
        if not callable(answer):
            return answer

        @functools.wraps(answer)
        def counted(*args: Any, **kwargs: Any) -> Any:
            self.calls[name] += 1
            return answer(*args, **kwargs)

        return counted

    @property
    def total(self) -> int:
        return sum(self.calls.values())


@dataclass(frozen=True)
class Experiment:
    """One configuration, run, with what it scored and what it spent."""

    setup: Setup
    result: EvaluationResult
    wall_seconds: float
    asked: dict[str, int]
    """How many times each measurement was asked for."""

    def config(self) -> dict[str, Any]:
        """Flat scalars only. A nested config is a config ARIA cannot plot."""
        return {
            **self.setup.fields(),
            "rulepack_version": self.result.rulepack_version,
            "cases": len(self.result.outcomes),
        }

    def metrics(self) -> dict[str, Any]:
        """The scores under the names scorers.py gives them, then the cost.

        A metric means the same thing here as it does in the Evals tab, so the
        scorer names are left alone and everything else is grouped under a
        prefix W&B reads as a section.
        """
        return {**self.result.scores, **self._cost(), **self._asked()}

    def _cost(self) -> dict[str, Any]:
        fixes = [o.fix for o in self.result.outcomes if o.fix is not None]
        cases = len(self.result.outcomes) or 1
        return {
            "cost/measurements_taken": sum(self.asked.values()),
            "cost/candidates_measured": sum(fix.measured for fix in fixes),
            "cost/wall_seconds": round(self.wall_seconds, 2),
            "cost/seconds_per_case": round(self.wall_seconds / cases, 3),
            "cost/fixes_attempted": len(fixes),
            "cost/fixes_found": sum(1 for fix in fixes if fix.found),
            "cost/cases_failed": len(self.result.failures),
            "cost/completed": self.result.completed,
        }

    def _asked(self) -> dict[str, int]:
        return {f"measurements/{name}": n for name, n in sorted(self.asked.items())}

    def table_rows(self) -> list[list[Any]]:
        return [
            [_cell(row.get(column)) for column in TABLE_COLUMNS]
            for row in self.result.rows()
        ]


def _cell(value: Any) -> Any:
    """A table cell is a number, a string or nothing."""
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(str(item) for item in sorted(value))
    return value


def grid(**axes: tuple[Any, ...]) -> list[Setup]:
    """Every combination of the given knobs, each named after what it sets."""
    names = list(axes)
    return [
        setup(**dict(zip(names, values)))
        for values in itertools.product(*(axes[name] for name in names))
    ]


CONTROL = setup(measurements="stub")
"""The rules scored against the fixtures' simplified measurements.

`FixtureMeasurements` merges axis-aligned boxes along a straight leg, which is
right for the shipped shop and wrong for cases that move the geometry, so this
is a control rather than a rival: reading it against the sweep says how much of
the score is the measurement. It ignores cell size, so it is one run and not a
second axis."""

DEFAULT_GRID = (*grid(**GRID_AXES), CONTROL)


def run_experiment(
    configuration: Setup, cases: list[Case] | None = None
) -> Experiment:
    """One configuration against every case, counted and timed."""
    measure = Counted(
        new_measurements(configuration.measurements, configuration.cell_size)
    )
    started = time.monotonic()
    result = evaluate(
        measure=measure,
        rules=load_pack(),
        ledger=ledger(configuration.preview_unverified),
        router=router(configuration.router),
        cases=cases,
        run_fixes=configuration.run_fixes,
        fix_candidates=configuration.fix_candidates,
        publish=False,
    )
    return Experiment(
        configuration, result, time.monotonic() - started, dict(measure.calls)
    )


def run_grid(
    setups: list[Setup] | tuple[Setup, ...] | None = None,
    *,
    cases: list[Case] | None = None,
) -> list[Experiment]:
    picked = list(cases if cases is not None else dataset())
    return [
        run_experiment(configuration, picked)
        for configuration in (setups if setups is not None else DEFAULT_GRID)
    ]


NO_PROJECT = (
    "Set WANDB_PROJECT to the project these runs belong in, and WANDB_ENTITY "
    "to the team that owns it."
)

NOT_INSTALLED = (
    "Install the SDK: python -m pip install -e 'packages/agents[observability]'."
)

NOT_SIGNED_IN = "Sign in with wandb login, or put WANDB_API_KEY in .env."


def target(
    project: str | None = None, entity: str | None = None
) -> tuple[str, str | None] | None:
    """Which project and team to log to, or nothing when no project is named.

    `WANDB_PROJECT` holds either a name or `entity/name`, because that is what
    `weave.init` takes and the two share one project. The team may be left to
    wandb, which falls back to the default entity of whoever is signed in —
    worth knowing, because that default is a personal entity and ARIA only
    answers in a team project.
    """
    name = project or os.environ.get(PROJECT_ENV)
    if not name:
        return None
    team = entity or os.environ.get(ENTITY_ENV)
    if "/" in name:
        team, name = name.split("/", 1)
    return name, team


def signed_in(module: Any) -> bool:
    """Whether wandb can authenticate without asking anybody.

    A key in the environment, a `wandb login` that wrote one to `~/.netrc`, or
    a run going to a directory instead of to the cloud. `wandb.init` prompts
    for a key when it finds none, which would hang a command nobody is
    watching, so this is asked before init rather than caught after it.
    """
    if os.environ.get(KEY_ENV) or os.environ.get(MODE_ENV) == "offline":
        return True
    return _holds_a_key(module)


def _holds_a_key(module: Any) -> bool:
    """`login(prompt=False)` is how current wandb answers this without asking.

    Older versions answer with `wandb.api.api_key`, which current ones warn
    about, so it is the fallback rather than the first ask. `verify` stays off
    because this decides whether to try, and a key the server rejects is
    already handled where the run is made.
    """
    login = getattr(module, "login", None)
    if login is not None:
        try:
            return bool(login(prompt=False, verify=False))
        except TypeError:
            pass
    return bool(getattr(getattr(module, "api", None), "api_key", None))


def blocker(
    project: str | None = None, entity: str | None = None
) -> str | None:
    """What to do so these runs land in W&B, or nothing when they will."""
    if target(project, entity) is None:
        return NO_PROJECT
    module = _import_wandb()
    if module is None:
        return NOT_INSTALLED
    if not signed_in(module):
        return NOT_SIGNED_IN
    return None


def log_experiments(
    experiments: list[Experiment],
    *,
    project: str | None = None,
    entity: str | None = None,
    group: str = GROUP,
) -> list[str]:
    """One W&B run per configuration. Returns the URL of each.

    Every failure here is a third party's: no account, no network, a rejected
    key, an SDK that moved. None of them may discard numbers that have already
    been computed, so a failure is logged and the local record stands.
    """
    if blocker(project, entity) is not None:
        return []
    where = target(project, entity)
    module = _import_wandb()
    urls = []
    for experiment in experiments:
        url = _log_one(module, experiment, where, group)
        if url:
            urls.append(url)
    return urls


def _log_one(
    module: Any, experiment: Experiment, where: tuple[str, str | None], group: str
) -> str | None:
    name, team = where
    try:
        run = module.init(
            project=name,
            entity=team,
            name=experiment.setup.label,
            group=group,
            job_type=JOB_TYPE,
            tags=list(TAGS),
            config=experiment.config(),
            notes=NOTES,
        )
    except Exception as error:
        log.warning("this configuration stayed local, %s said: %s", name, error)
        return None
    run.log(
        {
            **experiment.metrics(),
            "cases": module.Table(
                columns=list(TABLE_COLUMNS), data=experiment.table_rows()
            ),
        }
    )
    url = getattr(run, "url", None)
    run.finish()
    return url


def save_experiments(experiments: list[Experiment], path: Path) -> Path:
    """The grid on disk, one object per configuration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "lower_is_better": sorted(LOWER_IS_BETTER),
                "runs": [
                    {
                        "label": experiment.setup.label,
                        "config": experiment.config(),
                        "metrics": experiment.metrics(),
                        "failures": experiment.result.failures,
                    }
                    for experiment in experiments
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _import_wandb() -> Any | None:
    try:
        import wandb
    except ImportError:
        return None
    return wandb


__all__ = [
    "DEFAULT_GRID", "GRID_AXES", "GROUP", "JOB_TYPE", "KEY_ENV", "MODE_ENV",
    "NOTES", "NOT_INSTALLED", "NOT_SIGNED_IN", "NO_PROJECT", "TABLE_COLUMNS",
    "TAGS", "Counted", "Experiment", "blocker", "grid", "log_experiments",
    "run_experiment", "run_grid", "save_experiments", "signed_in", "target",
]
