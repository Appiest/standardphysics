"""Place furniture around a required clear space, then measure the whole room.

The small slide ladder cannot escape a corner or move several chairs out of
one turning space. This bounded beam tries positions and quarter turns
together. A table travels with the chairs pulled up to it, every piece lands
square to the walls, and guesses are ranked by `Composition.cost`, so a tidy
arrangement is measured before a cheap crooked one. Geometry only generates
candidates; the search's assessment gate accepts them.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from standardphysics_contracts import Finding, NodeMove, SceneGraph, SceneNode, Vec3, to_meters
from standardphysics_pipeline import footprint, gap_between
from standardphysics_pipeline.footprints import Polygon, polygon_bounds, rotation_about_z

from ..checks.rectangles import rectangle
from ..rules import AgentRulePack
from .composition import Composition, carried, squaring_turn
from .constraints import violations
from .moves import apply_moves, move_node
from .pinch import Pinch
from .strategies import Candidate

QUARTER_TURNS = (0.0, 90.0, -90.0, 180.0)
OPTIONS_PER_PIECE = 8
BEAM_WIDTH = 16
MAX_PIECES = 8

SLIDES = (0.0, -0.3, 0.3, -0.75, 0.75, -1.5, 1.5)
"""How far to slide along each side of the space, to get past neighbouring furniture."""

MARGIN = 0.06
"""A step beyond the clearance edge, with room for occupancy-grid rounding."""

WALL_GAP = 0.01
"""How far off a wall a piece set against it stands, so it does not touch the wall."""

SPOT_METERS = 0.2
"""Destinations closer than this count as one spot, so the beam keeps different places."""

Bounds = tuple[float, float, float, float]


@dataclass(frozen=True)
class _Unit:
    """A piece to place, with the chairs that travel with it when it is a table."""

    lead: SceneNode
    chairs: tuple[SceneNode, ...] = ()

    def moves(self, move: NodeMove) -> list[NodeMove]:
        return [move, *carried(self.lead, move, self.chairs)]

    def shapes(self, move: NodeMove) -> list[Polygon]:
        members = {node.id: node for node in (self.lead, *self.chairs)}
        return [footprint(move_node(members[m.node_id], m)) for m in self.moves(move)]


def _space(finding: Finding, graph: SceneGraph, rules: AgentRulePack) -> Polygon:
    """The required region, rather than the undersized region that was measured."""
    rule = rules.by_id(finding.check_id)
    width = depth = to_meters(finding.required_inches)
    rotation = (1.0, 0.0)
    if finding.check_id == "service_counter_approach":
        width = to_meters(rule.parameter("clear_width_min_inches"))
        depth = to_meters(rule.parameter("clear_depth_min_inches"))
        rotation = rotation_about_z(graph.by_id(finding.locus.node_ids[0]))
    return rectangle(finding.locus.point, width, depth, rotation)


def _units(graph: SceneGraph, pinch: Pinch, composition: Composition) -> list[_Unit]:
    """A table brings its chairs; a chair whose table stays put moves alone."""
    blocking = {node.id for node in pinch.movable}
    leads = sorted(
        (node for node in pinch.movable if composition.table_of.get(node.id) not in blocking),
        key=lambda node: str(node.id),
    )
    return [
        _Unit(lead, tuple(
            graph.by_id(chair) for chair, table in composition.table_of.items() if table == lead.id
        ))
        for lead in leads[:MAX_PIECES]
    ]


def _beside(space: Bounds, origin, reach: Bounds) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Centres that put the unit just past each side of the space."""
    low_x, low_y, high_x, high_y = space
    xs = (low_x - (reach[2] - origin.x) - MARGIN, high_x + (origin.x - reach[0]) + MARGIN)
    ys = (low_y - (reach[3] - origin.y) - MARGIN, high_y + (origin.y - reach[1]) + MARGIN)
    return xs, ys


def _against_walls(interior: Bounds | None, origin, reach: Bounds) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Centres that stand the unit against each wall of the room."""
    if interior is None:
        return (), ()
    low_x, low_y, high_x, high_y = interior
    xs = (low_x + (origin.x - reach[0]) + WALL_GAP, high_x - (reach[2] - origin.x) - WALL_GAP)
    ys = (low_y + (origin.y - reach[1]) + WALL_GAP, high_y - (reach[3] - origin.y) - WALL_GAP)
    return xs, ys


def _destinations(space: Bounds, interior: Bounds | None, origin, reach: Bounds) -> list[tuple[float, float]]:
    side_xs, side_ys = _beside(space, origin, reach)
    wall_xs, wall_ys = _against_walls(interior, origin, reach)
    xs, ys = (*side_xs, *wall_xs), (*side_ys, *wall_ys)
    points = [(origin.x, origin.y)]
    for offset in SLIDES:
        points.extend((x, origin.y + offset) for x in xs)
        points.extend((origin.x + offset, y) for y in ys)
    points.extend((x, y) for x in xs for y in ys)
    return points


def _options(unit: _Unit, space: Polygon, composition: Composition) -> list[list[NodeMove]]:
    """Every square-to-the-walls spot that keeps the whole unit out of the space, cheapest first."""
    origin = unit.lead.transform.position
    square = squaring_turn(unit.lead, composition.axis_degrees)
    options = []
    for quarter in QUARTER_TURNS:
        turn = NodeMove(node_id=unit.lead.id, delta_translation=Vec3(x=0, y=0, z=0),
                        delta_rotation_z_degrees=square + quarter)
        reach = polygon_bounds([point for shape in unit.shapes(turn) for point in shape])
        for x, y in _destinations(polygon_bounds(space), composition.interior, origin, reach):
            move = turn.model_copy(update={"delta_translation": Vec3(x=x - origin.x, y=y - origin.y, z=0)})
            if all(gap_between(shape, space) > 0 for shape in unit.shapes(move)):
                options.append(unit.moves(move))
    options.sort(key=composition.cost)
    return options


def _spot(graph: SceneGraph, option: list[NodeMove]) -> tuple[int, int]:
    lead = option[0]
    position = graph.by_id(lead.node_id).transform.position
    return (
        round((position.x + lead.delta_translation.x) / SPOT_METERS),
        round((position.y + lead.delta_translation.y) / SPOT_METERS),
    )


def _legal_spots(graph: SceneGraph, moves: list[NodeMove], options: list[list[NodeMove]]) -> list[list[NodeMove]]:
    """Up to `OPTIONS_PER_PIECE` distinct places this unit can go, given the moves already made."""
    found, taken = [], set()
    for option in options:
        spot = _spot(graph, option)
        if spot in taken:
            continue
        trial = [*moves, *option]
        if violations(graph, apply_moves(graph, trial)):
            continue
        found.append(trial)
        taken.add(spot)
        if len(found) == OPTIONS_PER_PIECE:
            break
    return found


def _beam(graph: SceneGraph, group: tuple[_Unit, ...], options, composition: Composition) -> list[Candidate]:
    beam: list[list[NodeMove]] = [[]]
    for unit in group:
        expanded = [trial for moves in beam for trial in _legal_spots(graph, moves, options[unit.lead.id])]
        beam = sorted(expanded, key=composition.cost)[:BEAM_WIDTH]
        if not beam:
            return []
    return [Candidate("place_furniture", moves, composition.cost(moves)) for moves in beam]


def _groups(units: list[_Unit]) -> list[tuple[_Unit, ...]]:
    """Single units first; then joint placements, even when no single move improves a zero-width space."""
    groups = [(unit,) for unit in units]
    if len(units) > 2:
        groups.append(tuple(units))
    groups.extend(combinations(units, 2))
    return groups


def placements(graph: SceneGraph, pinch: Pinch, finding: Finding,
               rules: AgentRulePack, limit: int) -> list[Candidate]:
    if limit <= 0 or not pinch.fixable:
        return []
    composition = Composition.of(graph)
    space = _space(finding, graph, rules)
    units = _units(graph, pinch, composition)
    options = {unit.lead.id: _options(unit, space, composition) for unit in units}
    found = [
        candidate
        for group in _groups(units)
        for candidate in _beam(graph, group, options, composition)
    ]
    found.sort(key=lambda candidate: candidate.disruption)
    return found[:limit]
