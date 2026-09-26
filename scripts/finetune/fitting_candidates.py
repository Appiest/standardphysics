"""Fitting edits the solver tries for problems no rearrangement clears, cheapest first.

Each problem the fittings scope measures gets a short list of answers that
could clear it on its own: a counter too high gets a lowered section cut into
either end with its registers carried onto it, then its existing section or the
whole counter lowered; too few tables at dining height get the fewest tables
closest to the range re-heighted, then replaced; a control out of reach is
rehung within it. The solver scores them; nothing here decides whether one
helped.
"""

from __future__ import annotations

from typing import Callable

from standardphysics_agents.checks import roles
from standardphysics_agents.checks.dining import required_count, surface_height_inches, within_range
from standardphysics_agents.checks.service_counter import ADJACENT_METERS
from standardphysics_agents.training.catalog import ACCESSIBLE_FOUR_TOP, ACCESSIBLE_TWO_TOP, LOWERED_COUNTER_SECTION
from standardphysics_agents.training.edits import TrainingEdits
from standardphysics_agents.training.fittings import HeightChange, LoweredSection, Replacement, rests_on
from standardphysics_contracts import SceneGraph, SceneNode, to_inches, to_meters
from standardphysics_pipeline import footprint, gap_between

ACCESSIBLE_TOP_INCHES = ACCESSIBLE_TWO_TOP.top_inches
SECTION_TOP_INCHES = LOWERED_COUNTER_SECTION.top_inches
REACH_MARGIN_INCHES = 0.5
"""Rehung controls land half an inch inside the reach range, clear of float noise at the limit."""
FOUR_TOP_FROM_METERS = to_meters(33.0)
"""A table wider than this is replaced by the four-top rather than the two-top."""


def _named(finding) -> set:
    return set(finding.locus.node_ids) if finding.locus else set()


def _sections_beside(graph: SceneGraph, counter: SceneNode) -> list[SceneNode]:
    return [node for node in roles.lowered_sections(graph)
            if gap_between(footprint(node), footprint(counter)) <= ADJACENT_METERS]


def _counter_candidates(graph: SceneGraph, checker, finding) -> list[TrainingEdits]:
    named = _named(finding)
    counters = [counter for counter in roles.service_counters(graph) if counter.id in named]
    candidates = []
    for counter in counters:
        carry = [item.id for item in roles.point_of_sale(graph) if rests_on(item, counter)]
        candidates += [TrainingEdits(add_lowered_section=[LoweredSection(counter_id=counter.id, end=end, carry=carry)])
                       for end in ("start", "end")]
        candidates += [TrainingEdits(height_changes=[HeightChange(node_id=section.id, top_inches=SECTION_TOP_INCHES)])
                       for section in _sections_beside(graph, counter)]
        candidates.append(TrainingEdits(height_changes=[HeightChange(node_id=counter.id,
                                                                     top_inches=SECTION_TOP_INCHES)]))
    return candidates


def _closest_to_range(tables: list[SceneNode], rule) -> list[SceneNode]:
    low, high = rule.parameter("surface_min_inches"), rule.parameter("surface_max_inches")

    def distance(table: SceneNode) -> float:
        height = surface_height_inches(table)
        return max(low - height, height - high, 0.0)

    return sorted((table for table in tables if not within_range(surface_height_inches(table), rule)), key=distance)


def _replacement_for(table: SceneNode) -> str:
    wide = max(table.dimensions.x, table.dimensions.y) > FOUR_TOP_FROM_METERS
    return (ACCESSIBLE_FOUR_TOP if wide else ACCESSIBLE_TWO_TOP).name


def _dining_candidates(graph: SceneGraph, checker, finding) -> list[TrainingEdits]:
    rule = checker.rules.by_id(finding.check_id)
    tables = [table for table in roles.dining_surfaces(graph) if table.id not in checker.pinned]
    complying = sum(1 for table in roles.dining_surfaces(graph) if within_range(surface_height_inches(table), rule))
    short = required_count(len(roles.dining_surfaces(graph)), rule) - complying
    chosen = _closest_to_range(tables, rule)[:max(short, 0)]
    if not chosen or len(chosen) < short:
        return []
    return [
        TrainingEdits(height_changes=[HeightChange(node_id=table.id, top_inches=ACCESSIBLE_TOP_INCHES) for table in chosen]),
        TrainingEdits(replacements=[Replacement(node_id=table.id, catalog_item=_replacement_for(table))
                                    for table in chosen]),
    ]


def _rehung_top(node: SceneNode, rule) -> float:
    bottom = to_inches(node.transform.position.z - node.dimensions.z / 2)
    tall = to_inches(node.dimensions.z)
    low = rule.parameter("unobstructed_low_inches")
    if bottom < low:
        return low + REACH_MARGIN_INCHES + tall
    return rule.threshold - REACH_MARGIN_INCHES


def _reach_candidates(graph: SceneGraph, checker, finding) -> list[TrainingEdits]:
    rule = checker.rules.by_id(finding.check_id)
    named = _named(finding)
    return [TrainingEdits(height_changes=[HeightChange(node_id=node.id, top_inches=_rehung_top(node, rule))])
            for node in roles.operable_parts(graph) if node.id in named and node.id not in checker.pinned]


CANDIDATES: dict[str, Callable[[SceneGraph, object, object], list[TrainingEdits]]] = {
    "service_counter_height": _counter_candidates,
    "dining_surface_height": _dining_candidates,
    "reach_range": _reach_candidates,
}


def fitting_candidates(graph: SceneGraph, checker) -> list[list[TrainingEdits]]:
    """For each problem only a fitting edit can clear, the answers worth trying, cheapest first."""
    problems = checker.fixable_problems(checker.assess(graph))
    lists = [CANDIDATES[finding.check_id](graph, checker, finding) for finding in problems
             if finding.check_id in CANDIDATES]
    return [candidates for candidates in lists if candidates]
