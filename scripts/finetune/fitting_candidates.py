"""Fitting edits the solver tries for problems no rearrangement clears, cheapest first.

Each problem the fittings scope measures gets a short list of answers that
could clear it on its own: a counter too high gets a lowered section cut into
either end with its registers carried onto it, then its existing section or the
whole counter lowered; too few tables at dining height get the fewest tables
closest to the range re-heighted, then replaced; a control out of reach is
rehung within it; a register or card reader left on the high part of a counter
that already has a lowered section is set down on that section. The solver scores them; nothing here decides whether one
helped.
"""

from __future__ import annotations

from typing import Callable

from standardphysics_agents.checks import roles
from standardphysics_agents.checks.dining import required_count, surface_height_inches, within_range
from standardphysics_agents.checks.service_counter import ADJACENT_METERS
from standardphysics_agents.fix.moves import top_of
from standardphysics_agents.redesign import FurnitureMove
from standardphysics_agents.training.catalog import ACCESSIBLE_FOUR_TOP, ACCESSIBLE_TWO_TOP, LOWERED_COUNTER_SECTION
from standardphysics_agents.training.construction import FixtureMove, fixture_ids
from standardphysics_agents.training.edits import TrainingEdits
from standardphysics_agents.training.fittings import HeightChange, LoweredSection, Replacement, rests_on
from standardphysics_contracts import SceneGraph, SceneNode, to_inches, to_meters
from standardphysics_pipeline import footprint, gap_between

ACCESSIBLE_TOP_INCHES = ACCESSIBLE_TWO_TOP.top_inches
SECTION_TOP_INCHES = LOWERED_COUNTER_SECTION.top_inches
REACH_MARGIN_INCHES = 0.5
"""Rehung controls land half an inch inside the reach range, clear of float noise at the limit."""
SET_DOWN_STEP_METERS = 0.05
"""Spacing of the spots tried along a lowered section when setting a register down on it."""
SET_DOWN_CLEARANCE_METERS = 0.02
WALL_SLIDE_INCHES = (6.0, 12.0, 18.0, 24.0)
"""How far a control is slid along its wall to clear what is under it, nearest first."""
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


def _spots_along(section: SceneNode, item: SceneNode) -> list[tuple[float, float]]:
    """Centres along the section's long axis where the item fits wholly on its top, middle first."""
    long_axis_x = section.dimensions.x >= section.dimensions.y
    length = section.dimensions.x if long_axis_x else section.dimensions.y
    half = max(item.dimensions.x, item.dimensions.y) / 2 + SET_DOWN_CLEARANCE_METERS
    reach = length / 2 - half
    if reach < 0:
        return []
    steps = int(reach // SET_DOWN_STEP_METERS)
    offsets = sorted({step * SET_DOWN_STEP_METERS * sign for step in range(steps + 1) for sign in (1, -1)}, key=abs)
    cos_t, sin_t = section.transform.m[0], section.transform.m[4]
    along = (cos_t, sin_t) if long_axis_x else (-sin_t, cos_t)
    centre = section.transform.position
    return [(centre.x + along[0] * offset, centre.y + along[1] * offset) for offset in offsets]


def _free_spot(graph: SceneGraph, section: SceneNode, item: SceneNode) -> tuple[float, float] | None:
    others = [node for node in graph.nodes if node.id != item.id and rests_on(node, section)]
    for x, y in _spots_along(section, item):
        dx, dy = x - item.transform.position.x, y - item.transform.position.y
        moved = item.model_copy(update={"transform": item.transform.model_copy(update={"m": [
            *item.transform.m[:3], x, *item.transform.m[4:7], y, *item.transform.m[8:]]})})
        if all(gap_between(footprint(moved), footprint(other)) > SET_DOWN_CLEARANCE_METERS for other in others):
            return dx, dy
    return None


def _point_of_sale_candidates(graph: SceneGraph, checker, _finding) -> list[TrainingEdits]:
    """Each register or card reader on a high counter, set down on a free spot of the counter's lowered section."""
    sellers = [item for item in roles.point_of_sale(graph) if item.movable and item.id not in checker.pinned]
    candidates = []
    for counter in roles.service_counters(graph):
        for item in (seller for seller in sellers if rests_on(seller, counter)):
            for section in sorted(_sections_beside(graph, counter), key=top_of):
                spot = _free_spot(graph, section, item)
                if spot is not None:
                    candidates.append(TrainingEdits(moves=[FurnitureMove(node_id=item.id, dx=round(spot[0], 3),
                                                                         dy=round(spot[1], 3), rotation_degrees=0.0)]))
                    break
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


def _along_the_wall(node: SceneNode, inches: float) -> FixtureMove:
    """A slide along the item's own width, which for a mounted item runs along its wall."""
    cos_t, sin_t = node.transform.m[0], node.transform.m[4]
    return FixtureMove(node_id=node.id, dx_inches=round(cos_t * inches, 1), dy_inches=round(sin_t * inches, 1))


def _reach_candidates(graph: SceneGraph, checker, finding) -> list[TrainingEdits]:
    """Rehang each control within reach where it is; failing that, slide it along its wall first.

    A dispenser over a lavatory cannot come down far enough without meeting the
    basin, so the slides move it clear of whatever is under it before it drops.
    """
    rule = checker.rules.by_id(finding.check_id)
    named = _named(finding)
    fixtures = fixture_ids(graph)
    candidates = []
    for node in roles.operable_parts(graph):
        if node.id not in named or node.id in checker.pinned:
            continue
        lowered = HeightChange(node_id=node.id, top_inches=_rehung_top(node, rule))
        candidates.append(TrainingEdits(height_changes=[lowered]))
        if node.id in fixtures:
            candidates += [TrainingEdits(fixture_moves=[_along_the_wall(node, sign * inches)], height_changes=[lowered])
                           for inches in WALL_SLIDE_INCHES for sign in (1, -1)]
    return candidates


CANDIDATES: dict[str, Callable[[SceneGraph, object, object], list[TrainingEdits]]] = {
    "service_counter_height": _counter_candidates,
    "point_of_sale_height": _point_of_sale_candidates,
    "dining_surface_height": _dining_candidates,
    "reach_range": _reach_candidates,
}


def fitting_candidates(graph: SceneGraph, checker) -> list[list[TrainingEdits]]:
    """For each problem only a fitting edit can clear, the answers worth trying, cheapest first."""
    problems = checker.fixable_problems(checker.assess(graph))
    lists = [CANDIDATES[finding.check_id](graph, checker, finding) for finding in problems
             if finding.check_id in CANDIDATES]
    return [candidates for candidates in lists if candidates]
