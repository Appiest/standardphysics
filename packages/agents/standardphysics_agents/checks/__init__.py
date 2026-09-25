"""Every tier 1 check, and the one call that runs them.

A check runs only when a person has verified its rule, so the registry pairs
each function with the rules it answers rather than calling everything and
filtering afterwards.
"""

from __future__ import annotations

from typing import Callable, Iterable

from standardphysics_contracts.rules import Tier

from ..tracing import traced
from .context import CheckContext
from .dedupe import dedupe
from .dining import dining_surface_height
from .door_clearance import door_maneuvering_clearance, door_verdict
from .door_width import door_clear_width
from .exit_path import exit_path
from .observation import Observation, Unevaluated
from .passing_space import passing_space
from .protrusions import protruding_objects
from .questions import RULE_IDS as QUESTION_RULE_IDS
from .questions import scan_cannot_see
from .restroom import restroom_turning_space
from .result import CheckResult, as_result
from .route_width import route_clear_width, route_width_verdict
from .service_counter import (
    point_of_sale_height,
    service_counter_approach,
    service_counter_height,
)
from .turn_width import turn_clear_width, turn_verdict
from .turning_space import turning_space

CheckFn = Callable[[CheckContext], CheckResult | Iterable[Observation]]

REGISTRY: tuple[tuple[frozenset[str], CheckFn], ...] = (
    (frozenset({"route_clear_width"}), route_clear_width),
    (frozenset({"turn_clear_width"}), turn_clear_width),
    (frozenset({"passing_space"}), passing_space),
    (frozenset({"turning_space"}), turning_space),
    (frozenset({"door_clear_width"}), door_clear_width),
    (frozenset({"service_counter_height"}), service_counter_height),
    (frozenset({"service_counter_approach"}), service_counter_approach),
    (frozenset({"point_of_sale_height"}), point_of_sale_height),
    (frozenset({"exit_path"}), exit_path),
    (frozenset({"door_maneuvering_clearance"}), door_maneuvering_clearance),
    (frozenset({"protruding_objects"}), protruding_objects),
    (frozenset({"dining_surface_height"}), dining_surface_height),
    (frozenset({"restroom_turning_space"}), restroom_turning_space),
    (QUESTION_RULE_IDS, scan_cannot_see),
)


COVERED: frozenset[str] = frozenset()
"""Filled in below, once the registry exists."""


def _waiting_on_a_check(ctx: CheckContext, max_tier: Tier) -> list[Unevaluated]:
    """Rules a person has verified that no check answers.

    A rule with nothing behind it produces no findings, which from the outside
    is indistinguishable from a shop that passes it. Every other silent pass in
    this lane is guarded; this is the guard for the one where the gap is ours.
    """
    enabled = {rule.id for rule in ctx.rules.enabled(ctx.ledger, max_tier)}
    return [
        Unevaluated(rule_id, "a check in packages/agents to answer it")
        for rule_id in sorted(enabled - COVERED)
    ]


def _waiting_on_a_reader(ctx: CheckContext, max_tier: Tier) -> list[Unevaluated]:
    """Rules we hold that nobody has read yet.

    An empty report and a clean shop look identical from the outside, so a rule
    that is switched off for want of a reader says so here. This is how the API
    and the team find out, and it never reaches the owner.
    """
    enabled = {rule.id for rule in ctx.rules.enabled(ctx.ledger, max_tier)}
    return [
        Unevaluated(
            rule.id,
            f"a person to read {rule.citation.authority} {rule.citation.section} "
            f"and confirm {rule.threshold:g} {rule.unit}: "
            f"cli rules verify {rule.id} --by \"<name>\"",
        )
        for rule in ctx.rules.within_tier(max_tier)
        if rule.id not in enabled
    ]


@traced("checks.run")
def run_checks(ctx: CheckContext, max_tier: Tier = 1) -> CheckResult:
    enabled = {rule.id for rule in ctx.rules.enabled(ctx.ledger, max_tier)}
    observations: list[Observation] = []
    unevaluated: list[Unevaluated] = [
        *_waiting_on_a_reader(ctx, max_tier),
        *_waiting_on_a_check(ctx, max_tier),
    ]

    for rule_ids, check in REGISTRY:
        if not rule_ids & enabled:
            continue
        result = as_result(check(ctx))
        observations.extend(o for o in result.observations if o.rule_id in enabled)
        unevaluated.extend(result.unevaluated)

    return CheckResult(dedupe(observations, ctx.rules), unevaluated)


COVERED = frozenset().union(*[rule_ids for rule_ids, _ in REGISTRY])


__all__ = [
    "COVERED", "CheckContext", "CheckResult", "Observation", "REGISTRY",
    "Unevaluated", "dedupe", "dining_surface_height", "door_clear_width",
    "door_maneuvering_clearance", "door_verdict", "exit_path", "passing_space",
    "point_of_sale_height", "protruding_objects", "restroom_turning_space", "route_clear_width",
    "route_width_verdict", "run_checks", "scan_cannot_see",
    "service_counter_approach", "service_counter_height", "turn_clear_width",
    "turn_verdict", "turning_space",
]
