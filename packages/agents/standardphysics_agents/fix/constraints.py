"""What a rearrangement is not allowed to do.

Plan section 10 names the hard constraints: walls, built-in counters, doors and
their swing, the floor boundary, confirmed sizes, owner locks, and the checks
themselves. The checks are enforced by re-running them. Everything else is
enforced here, before a candidate is measured at all, because a candidate that
puts a display case inside a wall has an excellent clear width and is not a
rearrangement anybody can carry out.

Two more keep a candidate honest about what it is for. A piece may not travel
far from where the scan found it, and a table or counter has to keep room for
somebody to pull up to it. Without them a table shoved flush into a corner
widens the aisle beside it, the checks see an improvement, and nobody can sit
at the table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from standardphysics_contracts import SceneGraph, SceneNode, Vec3, lies_flat, to_meters
from standardphysics_pipeline import footprint, gap_between
from standardphysics_pipeline.discovery.taxonomy import is_fixture_name
from standardphysics_pipeline.footprints import (
    Polygon,
    distance_outside,
    floor_polygon,
    polygon_bounds,
    rotation_about_z,
)
from standardphysics_pipeline.occupancy import blocks_floor

from ..checks import roles
from ..checks.rectangles import rectangle
from ..checks.walls import upright_walls
from ..hashing import inventory
from ..rules import load_pack
from .moves import floor_height, measured_position, rests_on_something, top_of, underside
from .use_space import Room, has_room_to_use, reach

FLOOR_MARGIN = 0.01
"""A centimetre of slack at the floor edge, for arithmetic rather than for room."""

OVERLAP_TOLERANCE = 0.005
"""How far two things may interpenetrate before it counts as a collision.

`gap_between` returns zero for touching and for overlapping alike, and plenty
of shop furniture is built flush against a wall. Testing a footprint shrunk by
5 mm separates the two cases: touching leaves a 5 mm gap, real overlap leaves
none. 5 mm is a fifth of an inch, well under anything a scan resolves.

The shrunk shape is a test shape. Nothing proposed is ever built from it.
"""

VERTICAL_TOLERANCE = 0.02
"""How far a piece may sink into the one under it and still count as resting on it."""

SWING_KINDS = frozenset({"door"})

MAX_TRAVEL_METERS = to_meters(60.0)
"""How far a piece may end up from where the scan found it: 60 inches.

60 inches is the largest clear space any rule a rearrangement can fix asks
for: the turning circle of ADA 2010 304.3.1 and the passing space of 403.5.3.
Getting a piece out of such a space never needs it to travel further than the
space is wide. A piece carried further than that has been relocated to another
part of the room, which is a redesign for the owner to choose rather than a
fix, and it lands on floor the scan only ever saw around something else.

It is measured from `SceneNode.measured_position`, which every move carries
forward, so a run of small moves across rounds or saved revisions adds up
against it exactly as one long move would.
"""


def is_fixture(node: SceneNode) -> bool:
    return is_fixture_name(node.kind) or is_fixture_name(node.label)


@dataclass(frozen=True)
class Violation:
    kind: str
    node_id: str
    detail: str
    blocker: str | None = None
    """What the moved piece ran into, when something did."""


def _moved_nodes(base: SceneGraph, candidate: SceneGraph) -> list[SceneNode]:
    before = {node.id: node for node in base.nodes}
    return [
        node
        for node in candidate.nodes
        if node.id in before and node.transform.m != before[node.id].transform.m
    ]


def _locked_moves(base: SceneGraph, candidate: SceneGraph) -> list[Violation]:
    before = {node.id: node for node in base.nodes}
    return [
        Violation("moved_something_fixed", str(node.id), node.label)
        for node in _moved_nodes(base, candidate)
        if not before[node.id].movable or is_fixture(before[node.id])
    ]


def _resizes(base: SceneGraph, candidate: SceneGraph) -> list[Violation]:
    before = {node.id: node for node in base.nodes}
    found = []
    for node in candidate.nodes:
        original = before.get(node.id)
        if original and node.dimensions != original.dimensions:
            found.append(Violation("resized", str(node.id), node.label))
    return found


def _without(graph: SceneGraph, node_ids) -> SceneGraph:
    if not node_ids:
        return graph
    return graph.model_copy(
        update={"nodes": [n for n in graph.nodes if n.id not in node_ids]}
    )


def _inventory_changes(
    base: SceneGraph, candidate: SceneGraph, added
) -> list[Violation]:
    was, now = inventory(base), inventory(_without(candidate, added))
    return [
        Violation("inventory_changed", label, f"{was.get(label, 0)} -> {now.get(label, 0)}")
        for label in sorted(set(was) | set(now))
        if was.get(label, 0) != now.get(label, 0)
    ]


def floor_bounds(graph: SceneGraph) -> tuple[float, float, float, float] | None:
    for node in graph.nodes:
        if lies_flat(node):
            return polygon_bounds(floor_polygon(node))
    return None


def interior_bounds(graph: SceneGraph) -> tuple[float, float, float, float] | None:
    """The floor somebody can actually stand on, inside the walls.

    The floor node and the walls overlap: a wall straddles the edge of the
    floor it stands on, so half its thickness is inside the room. Placing
    furniture against the floor boundary puts it inside a wall, which is why
    this trims each side back to the wall's inner face.
    """
    bounds = floor_bounds(graph)
    if bounds is None:
        return None
    min_x, min_y, max_x, max_y = bounds
    centre_x, centre_y = (min_x + max_x) / 2, (min_y + max_y) / 2

    for wall in upright_walls(graph):
        shape = footprint(wall)
        low_x, high_x = min(x for x, _ in shape), max(x for x, _ in shape)
        low_y, high_y = min(y for _, y in shape), max(y for _, y in shape)
        if high_y - low_y >= high_x - low_x:
            if (low_x + high_x) / 2 < centre_x:
                min_x = max(min_x, high_x)
            else:
                max_x = min(max_x, low_x)
        elif (low_y + high_y) / 2 < centre_y:
            min_y = max(min_y, high_y)
        else:
            max_y = min(max_y, low_y)
    return min_x, min_y, max_x, max_y


def _outside_by(boundary: Polygon, node: SceneNode) -> float:
    return max(distance_outside(boundary, corner, FLOOR_MARGIN) for corner in footprint(node))


def _off_the_floor(base: SceneGraph, candidate: SceneGraph, checked: list[SceneNode]) -> list[Violation]:
    """Pieces a move pushes further past the edge of the floor.

    A scan's floor outline is an approximation, and RoomPlan regularly leaves a
    chair or a lamp hanging a few inches over it. Such a piece may still move,
    as long as the move does not carry it further out than the scan found it.
    """
    floor = next((node for node in candidate.nodes if lies_flat(node)), None)
    if floor is None:
        return []
    boundary = floor_polygon(floor)
    before = {node.id: node for node in base.nodes}
    found = []
    for node in checked:
        was_outside = _outside_by(boundary, before[node.id]) if node.id in before else 0.0
        if _outside_by(boundary, node) > was_outside + FLOOR_MARGIN:
            found.append(Violation("left_the_floor", str(node.id), node.label, blocker="wall"))
    return found


def door_keep_clear(door: SceneNode) -> Polygon:
    """The floor a door sweeps, as a square the width of the opening.

    A hinged door needs a quarter disc of radius equal to its width. A square
    of that side covers it and a little more, which errs toward keeping
    furniture further from a door rather than closer.
    """
    centre = door.transform.position
    reach = max(door.dimensions.x, door.dimensions.y)
    swings_along_x = door.dimensions.x >= door.dimensions.y
    half_x = reach / 2 if swings_along_x else reach
    half_y = reach if swings_along_x else reach / 2
    return [
        (centre.x - half_x, centre.y - half_y),
        (centre.x + half_x, centre.y - half_y),
        (centre.x + half_x, centre.y + half_y),
        (centre.x - half_x, centre.y + half_y),
    ]


def collision_shape(node: SceneNode, tolerance: float = OVERLAP_TOLERANCE) -> Polygon:
    """The node's footprint, pulled in on every side by `tolerance`."""
    shrunk = Vec3(
        x=max(node.dimensions.x - 2 * tolerance, 1e-6),
        y=max(node.dimensions.y - 2 * tolerance, 1e-6),
        z=node.dimensions.z,
    )
    return footprint(node.model_copy(update={"dimensions": shrunk}))


def _one_above_the_other(a: SceneNode, b: SceneNode) -> bool:
    """A laptop on a desk shares the desk's footprint without touching its body."""
    return underside(a) >= top_of(b) - VERTICAL_TOLERANCE or underside(b) >= top_of(a) - VERTICAL_TOLERANCE


def _overlapping(a: SceneNode, b: SceneNode) -> bool:
    # Each shape gives up half the tolerance, so together the two may
    # interpenetrate by OVERLAP_TOLERANCE and no more. Pulling both in by the
    # whole of it allowed twice that: a case slid 9 mm into a wall passed.
    half = OVERLAP_TOLERANCE / 2
    return (
        gap_between(collision_shape(a, half), collision_shape(b, half)) == 0.0
        and not _one_above_the_other(a, b)
    )


def _in_swing(node: SceneNode, keep_clear: Polygon, floor_z: float) -> bool:
    """Only something standing on the floor gets in the way of a door."""
    return not rests_on_something(node, floor_z) and gap_between(collision_shape(node), keep_clear) == 0.0


@dataclass(frozen=True)
class _Scene:
    """The layout a move started from, for telling a new clash from one the scan already had."""

    before: dict
    floor_z: float

    def already(self, clash, node: SceneNode, other: SceneNode) -> bool:
        was, other_was = self.before.get(node.id), self.before.get(other.id, other)
        return was is not None and clash(was, other_was)


def _collisions(base: SceneGraph, candidate: SceneGraph, moved: list[SceneNode]) -> list[Violation]:
    moved_ids = {node.id for node in moved}
    wall_ids = {node.id for node in upright_walls(candidate)}
    obstacles = [
        node
        for node in candidate.nodes
        if node.id not in moved_ids
        and (blocks_floor(node) or node.id in wall_ids)
    ]
    swings = [node for node in candidate.nodes if node.kind in SWING_KINDS]
    scene = _Scene(before={node.id: node for node in base.nodes}, floor_z=floor_height(base))

    found = []
    for index, node in enumerate(moved):
        found.extend(_overlaps(node, [*obstacles, *moved[index + 1:]], swings, scene))
    return found


def _overlaps(node: SceneNode, obstacles, swings, scene: _Scene) -> list[Violation]:
    for other in obstacles:
        if _overlapping(node, other) and not scene.already(_overlapping, node, other):
            return [Violation("collided", str(node.id), f"{node.label} into {other.label}", blocker=other.label)]
    for door in swings:
        clash = lambda piece, swing: _in_swing(piece, door_keep_clear(swing), scene.floor_z)
        if clash(node, door) and not scene.already(clash, node, door):
            return [Violation("blocked_a_door", str(node.id), f"{node.label} into the {door.label}", blocker=door.label)]
    return []


def _pos_space(counter: SceneNode) -> Polygon:
    rule = load_pack().by_id("service_counter_approach")
    width = to_meters(rule.parameter("clear_width_min_inches"))
    depth = to_meters(rule.parameter("clear_depth_min_inches"))
    cos_t, sin_t = rotation_about_z(counter)
    origin = counter.transform.position
    centre = Vec3(
        x=origin.x + sin_t * (counter.dimensions.y + depth) / 2,
        y=origin.y - cos_t * (counter.dimensions.y + depth) / 2,
        z=0.0,
    )
    return rectangle(centre, width - OVERLAP_TOLERANCE, depth - OVERLAP_TOLERANCE, (cos_t, sin_t))


def _keep_clear_zones(graph: SceneGraph) -> list[tuple[str, Polygon]]:
    zones = [(node.label, footprint(node)) for node in graph.nodes
             if node.kind.casefold() in {"ramp", "landing", "ramp_landing"}
             or node.label.strip().casefold() in {"ramp", "ramp landing", "accessible ramp", "landing"}]
    surfaces = roles.service_counters(graph) + roles.lowered_sections(graph)
    for reader in roles.point_of_sale(graph):
        for surface in surfaces:
            if (reader.parent_id == surface.id
                    or gap_between(footprint(reader), footprint(surface)) == 0.0):
                zones.append((f"{surface.label} payment approach", _pos_space(surface)))
                break
    return zones


def _blocked_keep_clear(base: SceneGraph, candidate: SceneGraph, moved: list[SceneNode]) -> list[Violation]:
    original = {node.id: node for node in base.nodes}
    zones = _keep_clear_zones(base)
    floor_z = floor_height(base)
    found = []
    for node in moved:
        if rests_on_something(node, floor_z) or is_fixture(node):
            continue
        for title, region in zones:
            now = gap_between(collision_shape(node), region) == 0.0
            was = node.id in original and gap_between(collision_shape(original[node.id]), region) == 0.0
            if now and not was:
                found.append(Violation("blocked_keep_clear", str(node.id), f"{node.label} into {title}", blocker=title))
    return found


def _travelled_too_far(base: SceneGraph, moved: list[SceneNode]) -> list[Violation]:
    """Pieces that ended up more than `MAX_TRAVEL_METERS` from where they were measured.

    The starting point comes from the base layout, not the candidate, so a
    candidate cannot move the goalposts by rewriting its own record.
    """
    before = {node.id: node for node in base.nodes}
    found = []
    for node in moved:
        origin, now = measured_position(before[node.id]), node.transform.position
        travelled = math.hypot(now.x - origin.x, now.y - origin.y)
        if travelled > MAX_TRAVEL_METERS:
            found.append(Violation("moved_too_far", str(node.id), node.label))
    return found


def _near_a_move(node: SceneNode, role: roles.UsedFromTheFloor, checked: list[SceneNode]) -> bool:
    shape, within = footprint(node), reach(role)
    return any(
        other.id == node.id or gap_between(shape, footprint(other)) < within for other in checked
    )


def _could_lose_room(candidate: SceneGraph, checked: list[SceneNode]) -> list[tuple[SceneNode, roles.UsedFromTheFloor]]:
    """Pieces used from the floor that moved, or that something moved next to."""
    found = []
    for node in candidate.nodes:
        role = roles.used_from_the_floor(node)
        if role is not None and _near_a_move(node, role, checked):
            found.append((node, role))
    return found


def _lost_room_to_use(base: SceneGraph, candidate: SceneGraph, checked: list[SceneNode]) -> list[Violation]:
    """Tables and counters a move leaves nobody room to pull up to.

    Like a collision, only a loss the move caused counts. A table the scan
    found wedged in already may still be moved, because the move takes no room
    from it that it had. A piece that is new has to arrive with room.
    """
    at_risk = _could_lose_room(candidate, checked)
    if not at_risk:
        return []
    before = {node.id: node for node in base.nodes}
    room_before, room_after = Room.of(base), Room.of(candidate)
    return [
        Violation("no_room_to_use", str(node.id), node.label)
        for node, role in at_risk
        if _had_room(room_before, before.get(node.id), role) and not has_room_to_use(room_after, node, role)
    ]


def _had_room(room: Room, node: SceneNode | None, role: roles.UsedFromTheFloor) -> bool:
    return node is None or has_room_to_use(room, node, role)


def violations(
    base: SceneGraph, candidate: SceneGraph, added: frozenset = frozenset()
) -> list[Violation]:
    """Every hard constraint the candidate breaks, or an empty list.

    `added` names pieces that are meant to be new, which is how "do I have room
    for a 97 inch couch" is asked. They do not count against the inventory, and
    they are checked for collisions and floor bounds exactly like a piece that
    moved: a candidate nobody tested for collisions fits everywhere.
    """
    moved = _moved_nodes(base, candidate)
    checked = [*moved, *[node for node in candidate.nodes if node.id in added]]
    return [
        *_locked_moves(base, candidate),
        *_resizes(base, candidate),
        *_inventory_changes(base, candidate, added),
        *_off_the_floor(base, candidate, checked),
        *_collisions(base, candidate, checked),
        *_blocked_keep_clear(base, candidate, checked),
        *_travelled_too_far(base, moved),
        *_lost_room_to_use(base, candidate, checked),
    ]


def is_allowed(base: SceneGraph, candidate: SceneGraph) -> bool:
    return not violations(base, candidate)
