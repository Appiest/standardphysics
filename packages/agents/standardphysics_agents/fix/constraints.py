"""What a rearrangement is not allowed to do.

Plan section 10 names the hard constraints: walls, built-in counters, doors and
their swing, the floor boundary, confirmed sizes, owner locks, and the checks
themselves. The checks are enforced by re-running them. Everything else is
enforced here, before a candidate is measured at all, because a candidate that
puts a display case inside a wall has an excellent clear width and is not a
rearrangement anybody can carry out.
"""

from __future__ import annotations

from dataclasses import dataclass

from standardphysics_contracts import SceneGraph, SceneNode, Vec3, lies_flat
from standardphysics_pipeline import footprint, gap_between
from standardphysics_pipeline.footprints import Polygon, distance_outside, floor_polygon, polygon_bounds
from standardphysics_pipeline.occupancy import blocks_floor

from ..checks.walls import upright_walls
from ..hashing import inventory
from .moves import floor_height, rests_on_something, top_of, underside

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
        if not before[node.id].movable
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
    return gap_between(collision_shape(a), collision_shape(b)) == 0.0 and not _one_above_the_other(a, b)


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


def violations(
    base: SceneGraph, candidate: SceneGraph, added: frozenset = frozenset()
) -> list[Violation]:
    """Every hard constraint the candidate breaks, or an empty list.

    `added` names pieces that are meant to be new, which is how "do I have room
    for a 97 inch couch" is asked. They do not count against the inventory, and
    they are checked for collisions and floor bounds exactly like a piece that
    moved: a candidate nobody tested for collisions fits everywhere.
    """
    checked = [
        *_moved_nodes(base, candidate),
        *[node for node in candidate.nodes if node.id in added],
    ]
    return [
        *_locked_moves(base, candidate),
        *_resizes(base, candidate),
        *_inventory_changes(base, candidate, added),
        *_off_the_floor(base, candidate, checked),
        *_collisions(base, candidate, checked),
    ]


def is_allowed(base: SceneGraph, candidate: SceneGraph) -> bool:
    return not violations(base, candidate)
