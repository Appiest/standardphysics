"""Directive constraint checker.

Checks the parts of a directive that the rulepack's physical checks do not:
roles a proposal may not move, 226.1's share of accessible dining surfaces, and
226.2's dispersion of those surfaces. Thresholds like counter height and route
width are measured by the rulepack checks, which the fix search already re-runs
on every candidate.

Dining surfaces are judged on height alone (902.3, 28 to 34 inches). Knee
clearance under a table is not visible in a bounding box, so it stays a
measurement request in the inspection manifest rather than a guess here.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import cache

from standardphysics_contracts import SceneGraph, SceneNode
from standardphysics_contracts.precedents import PrecedentDirective, PrecedentViolation

from ..checks import roles
from ..checks.dining import RULE_ID as DINING_RULE_ID
from ..checks.dining import required_count, surface_height_inches, within_range
from ..rules import RuleSpec, load_pack

FIXED_ROLE_FINDERS: dict[str, Callable[[SceneGraph], list[SceneNode]]] = {
    "service_counter": roles.service_counters,
    "point_of_sale": roles.point_of_sale,
}

HELD_EVEN_IF_MARKED_MOVABLE = frozenset({"service_counter"})
"""Roles held still whatever the scan says about them.

A counter is built in even when a scan marks it movable, so it stays put. A
point of sale is held only when it is built in, such as a fixed register: a
card reader or tip jar that can be picked up may be carried to the lowered
counter section, which is the fix ADA 904.4's advisory describes and the one
`point_of_sale_height` asks for.
"""


def _held(role: str, node: SceneNode) -> bool:
    return role in HELD_EVEN_IF_MARKED_MOVABLE or not node.movable

DISPERSION_RADIUS_METERS = 5.0
"""How far an accessible surface may sit from the middle of the other seating.

226.2 asks for dispersion without a distance. Five metres is this checker's
stand-in for "set apart from everyone else", not a number from the standard.
"""


@cache
def _dining_rule() -> RuleSpec:
    return load_pack().by_id(DINING_RULE_ID)


def _moved_ids(base: SceneGraph, candidate: SceneGraph) -> set:
    before = {node.id: node.transform.m for node in base.nodes}
    return {
        node.id
        for node in candidate.nodes
        if node.id in before and node.transform.m != before[node.id]
    }


def _check_fixed_roles(
    directive: PrecedentDirective, base: SceneGraph, candidate: SceneGraph
) -> list[PrecedentViolation]:
    moved = _moved_ids(base, candidate)
    return [
        PrecedentViolation(
            directive_id=directive.directive_id,
            authority=directive.authority[0],
            rule_broken="moved_fixed_role",
            detail=f"{node.label} is a {role} and has to stay where it is.",
            target_node_id=str(node.id),
        )
        for role in directive.constraints.fixed_roles
        for node in FIXED_ROLE_FINDERS[role](base)
        if node.id in moved and _held(role, node)
    ]


def _check_accessible_share(
    directive: PrecedentDirective, surfaces: list[SceneNode], accessible: list[SceneNode]
) -> list[PrecedentViolation]:
    needed = required_count(len(surfaces), _dining_rule())
    if len(accessible) >= needed:
        return []
    return [
        PrecedentViolation(
            directive_id=directive.directive_id,
            authority="ADA_2010_226.1",
            rule_broken="too_few_accessible_dining_surfaces",
            detail=(
                f"{len(accessible)} of {len(surfaces)} dining surfaces are 28 to 34 inches high; "
                f"226.1 needs at least {needed}."
            ),
        )
    ]


def _check_dispersion(
    directive: PrecedentDirective, surfaces: list[SceneNode], accessible: list[SceneNode]
) -> list[PrecedentViolation]:
    general = [node for node in surfaces if node not in accessible]
    if not general or not accessible:
        return []
    centre_x = sum(node.transform.position.x for node in general) / len(general)
    centre_y = sum(node.transform.position.y for node in general) / len(general)
    violations = []
    for node in accessible:
        distance = math.hypot(node.transform.position.x - centre_x, node.transform.position.y - centre_y)
        if distance > DISPERSION_RADIUS_METERS:
            violations.append(
                PrecedentViolation(
                    directive_id=directive.directive_id,
                    authority="ADA_2010_226.2",
                    rule_broken="accessible_dining_set_apart",
                    detail=f"{node.label} sits {distance:.1f} m from the rest of the seating.",
                    target_node_id=str(node.id),
                )
            )
    return violations


def _check_dining(directive: PrecedentDirective, candidate: SceneGraph) -> list[PrecedentViolation]:
    surfaces = roles.dining_surfaces(candidate)
    if not directive.constraints.requires_accessible_dining or not surfaces:
        return []
    accessible = [node for node in surfaces if within_range(surface_height_inches(node), _dining_rule())]
    violations = _check_accessible_share(directive, surfaces, accessible)
    if directive.constraints.dispersed:
        violations.extend(_check_dispersion(directive, surfaces, accessible))
    return violations


def check_precedent_constraints(
    base: SceneGraph,
    candidate: SceneGraph,
    directives: list[PrecedentDirective],
) -> list[PrecedentViolation]:
    """Return every directive violation in the candidate layout."""
    violations: list[PrecedentViolation] = []
    for directive in directives:
        violations.extend(_check_fixed_roles(directive, base, candidate))
        violations.extend(_check_dining(directive, candidate))
    return violations


def _violation_key(violation: PrecedentViolation) -> tuple[str, str, str | None]:
    return (violation.directive_id, violation.rule_broken, violation.target_node_id)


def precedent_rejection_for(
    directives: list[PrecedentDirective],
) -> Callable[[SceneGraph, SceneGraph], str | None]:
    """A CandidateRejection for propose_fix: refuse any candidate that adds a violation.

    Violations the base layout already has are allowed to remain, since a
    rearrangement is not always able to fix them. Swapping one violation for a
    different one is still a new violation and is refused.
    """

    def _reject(base: SceneGraph, candidate: SceneGraph) -> str | None:
        existing = {_violation_key(v) for v in check_precedent_constraints(base, base, directives)}
        for violation in check_precedent_constraints(base, candidate, directives):
            if _violation_key(violation) not in existing:
                return f"precedent_violation:{violation.rule_broken}"
        return None

    return _reject
