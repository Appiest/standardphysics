"""ADA 2010 403.5.1. How narrow the tightest point of a journey gets.

The section is 36 inches with one exception, and the exception has a length
condition attached to it. A bottleneck measurement alone cannot settle that
condition, so a route between 32 and 36 inches fails here until the provider
reports how long the narrow run is. A check that passed on a condition it never
evaluated would be worse than one that reports a problem to look at.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from standardphysics_contracts import WidthResult
from standardphysics_pipeline import path_locus, width_locus

from ..rules import RuleSpec
from ..tracing import traced
from .context import CheckContext
from .observation import Observation

RULE_ID = "route_clear_width"


@dataclass(frozen=True)
class WidthVerdict:
    satisfied: bool
    reason: str


Run = tuple[float, float]
"""Where a stretch below the minimum starts and ends, in inches along a leg."""


def route_width_verdict(
    inches: float, reduced_runs: Sequence[Run] | None, rule: RuleSpec
) -> WidthVerdict:
    """403.5.1 and its exception, with no geometry in the way.

    The exception has two conditions: each reduced stretch is 24 inches long
    at most, and reduced stretches are separated by 48 inches of route at full
    width. Two 24 inch pinches 10 inches apart meet the first and not the
    second.
    """
    if rule.satisfied_by(inches):
        return WidthVerdict(True, "meets_minimum")
    if inches < rule.parameter("reduced_min_inches"):
        return WidthVerdict(False, "below_minimum")
    if reduced_runs is None:
        return WidthVerdict(False, "reduction_length_unknown")
    if any(end - start > rule.parameter("reduced_max_run_inches") for start, end in reduced_runs):
        return WidthVerdict(False, "reduction_too_long")
    if any(
        following[0] - previous[1] < rule.parameter("separating_segment_min_length_inches")
        for previous, following in pairwise(reduced_runs)
    ):
        return WidthVerdict(False, "reductions_too_close")
    return WidthVerdict(True, "reduction_permitted")


def reduced_runs(ctx: CheckContext, rule: RuleSpec, leg_index: int) -> list[Run] | None:
    """Every stretch of this leg below the section's minimum, in order."""
    measure_runs = getattr(ctx.measure, "route_runs_below", None)
    if measure_runs is None:
        return None
    return [
        (float(start), float(end))
        for start, end in measure_runs(ctx.graph, ctx.scenario, leg_index, rule.threshold)
    ]


def dedupe_key(result: WidthResult) -> tuple:
    """One gap is one finding, however many legs go through it.

    Sorted rather than a set, because a finding id is derived from this key and
    has to come out the same in every process.
    """
    if result.blocking_node_ids:
        return (RULE_ID, tuple(sorted(str(n) for n in result.blocking_node_ids)))
    pinch = result.pinch_point
    return (RULE_ID, round(pinch.x, 2), round(pinch.y, 2))


@traced("checks.route_clear_width")
def route_clear_width(ctx: CheckContext) -> list[Observation]:
    rule = ctx.rule(RULE_ID)
    return [_leg(ctx, rule, index) for index in ctx.legs()]


def _leg(ctx: CheckContext, rule: RuleSpec, index: int) -> Observation:
    result = ctx.measure.route_clear_width(ctx.graph, ctx.scenario, index)
    movable, fixed = ctx.labels_by_movability(result.blocking_node_ids)
    facts = {
        "origin": ctx.stop_name(index),
        "destination": ctx.stop_name(index + 1),
        "blockers": [ctx.label(node_id) for node_id in result.blocking_node_ids],
        "movable_blockers": movable,
        "fixed_blockers": fixed,
    }
    if not result.reachable:
        return _blocked(rule, result, facts)

    verdict = route_width_verdict(
        result.inches, reduced_runs(ctx, rule, index), rule
    )
    return Observation(
        rule_id=RULE_ID,
        satisfied=verdict.satisfied,
        measured_inches=result.inches,
        required_inches=rule.threshold,
        relied_on=tuple(result.blocking_node_ids),
        locus=width_locus(ctx.graph, result),
        facts=facts,
        dedupe_key=dedupe_key(result),
        reason=verdict.reason,
    )


def _blocked(rule: RuleSpec, result: WidthResult, facts: dict) -> Observation:
    return Observation(
        rule_id=RULE_ID,
        satisfied=False,
        measured_inches=None,
        required_inches=rule.threshold,
        relied_on=tuple(result.blocking_node_ids),
        locus=path_locus(result, label=facts["destination"]),
        facts=facts,
        dedupe_key=(RULE_ID, "unreachable", facts["destination"]),
        reason="unreachable",
    )
