"""Precedent Constraint Checker.

Evaluates proposed scene graph rearrangements against matched case precedents,
enforcing court-mandated remedies (e.g. accessible seating ratios, anti-isolation,
counter clearances) before a layout can pass the evaluation gate.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from standardphysics_contracts import SceneGraph, SceneNode, to_inches
from standardphysics_contracts.precedents import PrecedentDirective, PrecedentViolation


def _is_accessible_dining_surface(node: SceneNode) -> bool:
    """Check if a table or counter surface conforms to ADA 2010 §§ 902.1 / 306.3."""
    height_in = to_inches(node.dimensions.z)
    if not (26.0 <= height_in <= 35.0):
        return False
    width_in = to_inches(max(node.dimensions.x, node.dimensions.y))
    return width_in >= 28.0


def _check_forbidden_moves(
    directive: PrecedentDirective,
    moved_nodes: list[SceneNode],
) -> list[PrecedentViolation]:
    """Flag attempts to move fixtures barred by case injunctions."""
    violations: list[PrecedentViolation] = []
    if "do_not_move_fixed_bar_plumbing" in directive.constraints.forbidden_moves:
        for node in moved_nodes:
            if "bar" in node.label.lower() or "plumb" in node.label.lower():
                violations.append(
                    PrecedentViolation(
                        case_id=directive.case_id,
                        landmark_citation=directive.landmark_citation,
                        rule_broken="forbidden_move",
                        detail=f"Plumbed bar fixture {node.label} cannot be moved to resolve seating ratio.",
                        target_node_id=str(node.id),
                    )
                )
    return violations


def _check_seating_ratio(
    directive: PrecedentDirective,
    dining_surfaces: list[SceneNode],
) -> tuple[list[PrecedentViolation], list[SceneNode]]:
    """Verify accessible dining surface percentage meets court mandate."""
    violations: list[PrecedentViolation] = []
    accessible = [s for s in dining_surfaces if _is_accessible_dining_surface(s)]
    ratio = len(accessible) / len(dining_surfaces)
    min_ratio = directive.constraints.minimum_accessible_percentage
    if ratio < min_ratio:
        violations.append(
            PrecedentViolation(
                case_id=directive.case_id,
                landmark_citation=directive.landmark_citation,
                rule_broken="insufficient_accessible_seating_ratio",
                detail=(
                    f"Accessible seating ratio is {ratio:.1%} ({len(accessible)} of {len(dining_surfaces)}), "
                    f"below mandatory {min_ratio:.0%} required by {directive.landmark_citation}."
                ),
            )
        )
    return violations, accessible


def _check_anti_isolation(
    directive: PrecedentDirective,
    dining_surfaces: list[SceneNode],
    accessible: list[SceneNode],
) -> list[PrecedentViolation]:
    """Ensure accessible tables remain integrated in primary customer area."""
    violations: list[PrecedentViolation] = []
    general = [s for s in dining_surfaces if s not in accessible]
    if not general or not accessible:
        return violations

    gx = sum(n.transform.position.x for n in general) / len(general)
    gy = sum(n.transform.position.y for n in general) / len(general)
    for acc in accessible:
        ax, ay = acc.transform.position.x, acc.transform.position.y
        dist = math.hypot(ax - gx, ay - gy)
        if dist > 5.0:
            violations.append(
                PrecedentViolation(
                    case_id=directive.case_id,
                    landmark_citation=directive.landmark_citation,
                    rule_broken="anti_isolation_violation",
                    detail=(
                        f"Accessible dining surface {acc.label} is placed {dist:.1f}m away "
                        f"from general seating area, violating integration mandate in {directive.landmark_citation}."
                    ),
                    target_node_id=str(acc.id),
                )
            )
    return violations


def check_precedent_constraints(
    base: SceneGraph,
    candidate: SceneGraph,
    directives: list[PrecedentDirective],
) -> list[PrecedentViolation]:
    """Return any violations of matched case law precedents in the candidate layout."""
    violations: list[PrecedentViolation] = []
    base_by_id = {n.id: n for n in base.nodes}
    cand_by_id = {n.id: n for n in candidate.nodes}

    moved_nodes = [
        cand_by_id[nid]
        for nid in cand_by_id
        if nid in base_by_id and cand_by_id[nid].transform.m != base_by_id[nid].transform.m
    ]

    dining_surfaces = [
        n for n in candidate.nodes
        if any(k in n.label.lower() for k in ("table", "dining", "counter_table", "desk"))
    ]

    for directive in directives:
        violations.extend(_check_forbidden_moves(directive, moved_nodes))

        if directive.constraints.minimum_accessible_percentage > 0 and dining_surfaces:
            ratio_viols, accessible = _check_seating_ratio(directive, dining_surfaces)
            violations.extend(ratio_viols)

            if directive.constraints.anti_isolation and len(dining_surfaces) > 1:
                violations.extend(_check_anti_isolation(directive, dining_surfaces, accessible))

    return violations


def precedent_rejection_for(
    directives: list[PrecedentDirective],
) -> Callable[[SceneGraph, SceneGraph], str | None]:
    """Create a CandidateRejection callback for propose_fix in fix.search."""
    def _reject(base: SceneGraph, candidate: SceneGraph) -> str | None:
        violations = check_precedent_constraints(base, candidate, directives)
        if violations:
            return f"precedent_violation:{violations[0].rule_broken}"
        return None

    return _reject
