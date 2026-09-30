"""Ten of the Weave evaluation's cases, scored on every push.

The full evaluation only runs by hand and lands in W&B, so a check that got
worse used to show up there after it merged. This runs the same
`evaluate_in_weave` the `weave-eval` command calls, on the shipped
configuration (measured pipeline, local router, fixes on), and fails when a
mean crosses the bound it held when the slice was chosen.

Weave runs without `weave.init`, so nothing is traced or uploaded and no W&B
key is needed. The root conftest clears the model keys, and the offline guard
in tests/support fails the test if anything tries to leave the machine.
"""

from __future__ import annotations

import pytest
from standardphysics_agents.evaluation.configuration import Setup, case, review
from standardphysics_agents.evaluation.scorers import LOWER_IS_BETTER
from standardphysics_agents.evaluation.weave_eval import evaluate_in_weave

pytestmark = pytest.mark.filterwarnings("ignore:Accessing the 'model_fields' attribute:DeprecationWarning:weave")

SHIPPED = Setup("pipeline measurements, fixes on")

SLICE = (
    "door_30",
    "door_clearance_blocked",
    "aisle_35_9",
    "tables_crowd_the_aisle",
    "lawsuit_counter",
    "thin_case_east",
    "passing_space_absent",
    "protrusion_sticks_out",
    "clean_shop",
    "blocked_solid",
)

BOUNDS = {
    "finding_precision": 1.0,
    "finding_recall": 0.94,
    "question_recall": 1.0,
    "measurement_error_in": 0.05,
    "label_accuracy": 1.0,
    "router_action_match": 1.0,
    "trajectory_ok": 1.0,
    "fix_resolves_finding": 1.0,
}
"""A floor for each score, and a ceiling for the ones in LOWER_IS_BETTER.

On 30 September the slice scored 1.0 on everything but two. Recall was 0.944
because blocked_solid reports route_clear_width and not exit_path, whose rule
the shipped ledger has not verified yet. Measurement error was 0.0028 in, all
of it protrusion_sticks_out's 0.011 in. Losing one expected problem anywhere
takes recall to 0.889 or lower.
"""


def _mean(summary: dict, name: str) -> float | None:
    scored = summary.get(name)
    return scored.get("mean") if isinstance(scored, dict) else None


def _crosses(name: str, mean: float | None) -> bool:
    if mean is None:
        return True
    return mean > BOUNDS[name] if name in LOWER_IS_BETTER else mean < BOUNDS[name]


@pytest.fixture
def summary(monkeypatch) -> dict:
    """One case at a time: every case measures through the same cached pipeline provider."""
    pytest.importorskip("weave", reason="needs Weave: pip install -e 'packages/agents[observability]'")
    monkeypatch.setenv("WEAVE_PARALLELISM", "1")
    scored = evaluate_in_weave([SHIPPED], cases=[case(case_id) for case_id in SLICE])
    return scored[SHIPPED.label]


def test_every_score_holds_its_bound(summary):
    crossed = {
        name: f"{_mean(summary, name)} against a bound of {BOUNDS[name]}"
        for name in BOUNDS
        if _crosses(name, _mean(summary, name))
    }
    assert not crossed, f"the slice scored worse than it did on 30 September: {crossed}"


@pytest.mark.parametrize("case_id", SLICE)
def test_no_case_in_the_slice_raises(case_id):
    """A case that raises is scored against an empty ledger, which can hide it inside a mean."""
    assert review(SHIPPED, case_id)["error"] is None
