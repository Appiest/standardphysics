"""Applying a proposal to a layout.

A move translates across the room and turns about Z. Furniture standing on the
floor keeps its height. A piece that was resting on something else, a laptop on
a desk or a pillow on a sofa, settles onto whatever is under it where it lands,
or onto the floor when nothing is. Nothing here can change a dimension, because
there is no code path that writes one: the candidate node is copied from the
original with a new transform and a record of where the scan found it, and
nothing else.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Callable
from typing import TypeVar
from uuid import UUID

from standardphysics_contracts import Mat4, NodeMove, SceneGraph, SceneNode, Vec3, bounds_the_room, lies_flat
from standardphysics_pipeline import contains_point, footprint
from standardphysics_pipeline.footprints import rotation_about_z

RESTING_GAP = 0.12
"""A piece whose underside is more than this above the floor was resting on something."""

LAYOUTS_REMEMBERED = 4

Fact = TypeVar("Fact")

HAND_CARRIED_VOLUME = 0.3
"""Cubic metres of bounding box one person carries: a sign stand, a stanchion, a
bin, a stool or a high chair, and not a display case or a booth."""

HAND_CARRIED_HEIGHT = 1.3
HAND_CARRIED_SPAN = 0.9
"""Metres. Taller or longer than this and a piece is walked, not carried."""

RIDING_GAP = 0.05
"""A piece whose underside is within this of another's top is sitting on it."""


def _turned(node: SceneNode, degrees: float, position: Vec3) -> Mat4:
    """A transform holding the node's rotation about Z plus `degrees`.

    Furniture standing on a floor is a Z rotation and a translation, which is
    what RoomPlan reports and what a drag produces. A transform carrying
    anything else is not something a rearrangement should be composing with.
    """
    cos_t, sin_t = rotation_about_z(node)
    cos_d, sin_d = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    cos_new = cos_t * cos_d - sin_t * sin_d
    sin_new = sin_t * cos_d + cos_t * sin_d
    return Mat4(
        m=[
            cos_new, -sin_new, 0.0, position.x,
            sin_new, cos_new, 0.0, position.y,
            0.0, 0.0, 1.0, position.z,
            0.0, 0.0, 0.0, 1.0,
        ]
    )


def move_node(node: SceneNode, move: NodeMove) -> SceneNode:
    origin = node.transform.position
    moved_to = Vec3(
        x=origin.x + move.delta_translation.x,
        y=origin.y + move.delta_translation.y,
        z=origin.z + move.delta_translation.z,
    )
    return node.model_copy(
        update={
            "transform": _turned(node, move.delta_rotation_z_degrees, moved_to),
            "measured_position": measured_position(node),
        }
    )


def measured_position(node: SceneNode) -> Vec3:
    """Where the scan found the node, before any rearrangement moved it."""
    return node.measured_position or node.transform.position


def per_layout(compute: Callable[[SceneGraph], Fact]) -> Callable[[SceneGraph], Fact]:
    """`compute(graph)`, remembered for the last few layout objects.

    A search checks thousands of candidates against one base layout, and a fact
    about the base is the same every time. Layouts are never edited in place, so
    the same object means the same layout.
    """
    kept: list[tuple[SceneGraph, Fact]] = []

    @functools.wraps(compute)
    def remembered(graph: SceneGraph) -> Fact:
        for known, fact in kept:
            if known is graph:
                return fact
        fact = compute(graph)
        kept.insert(0, (graph, fact))
        del kept[LAYOUTS_REMEMBERED:]
        return fact

    return remembered


def carried_by_hand(node: SceneNode) -> bool:
    """A movable piece small enough for staff to pick up and set down anywhere in the room."""
    size = node.dimensions
    return (
        node.movable
        and not lies_flat(node)
        and not bounds_the_room(node)
        and size.z <= HAND_CARRIED_HEIGHT
        and max(size.x, size.y) <= HAND_CARRIED_SPAN
        and size.x * size.y * size.z <= HAND_CARRIED_VOLUME
    )


def floor_height(graph: SceneGraph) -> float:
    floor = next((node for node in graph.nodes if lies_flat(node)), None)
    return floor.transform.position.z if floor else 0.0


def underside(node: SceneNode) -> float:
    return node.transform.position.z - node.dimensions.z / 2


def top_of(node: SceneNode) -> float:
    return node.transform.position.z + node.dimensions.z / 2


def rests_on_something(node: SceneNode, floor_z: float) -> bool:
    return not bounds_the_room(node) and underside(node) > floor_z + RESTING_GAP


def surface_under(graph: SceneGraph, node: SceneNode, floor_z: float) -> float:
    """The highest top among the pieces directly under this one's centre."""
    centre = (node.transform.position.x, node.transform.position.y)
    tops = [
        top_of(other)
        for other in graph.nodes
        if other.id != node.id and not bounds_the_room(other) and contains_point(footprint(other), centre)
    ]
    return max([floor_z, *tops])


def settle(graph: SceneGraph, node: SceneNode, floor_z: float) -> SceneNode:
    """The node lowered or raised so its underside sits on the surface below it."""
    position = node.transform.position
    resting_at = surface_under(graph, node, floor_z) + node.dimensions.z / 2
    return node.model_copy(
        update={"transform": _turned(node, 0.0, Vec3(x=position.x, y=position.y, z=resting_at))}
    )


def apply_moves(graph: SceneGraph, moves: list[NodeMove]) -> SceneGraph:
    """A new layout. The original is never touched, so a rejected candidate
    cannot leave anything behind."""
    by_node: dict[UUID, NodeMove] = {move.node_id: move for move in moves}
    floor_z = floor_height(graph)
    resting = {node.id for node in graph.nodes if node.id in by_node and rests_on_something(node, floor_z)}
    moved = graph.model_copy(
        update={"nodes": [move_node(node, by_node[node.id]) if node.id in by_node else node for node in graph.nodes]}
    )
    nodes = [settle(moved, node, floor_z) if node.id in resting else node for node in moved.nodes]
    return graph.model_copy(
        update={"nodes": nodes, "revision": graph.revision + 1, "base_hash": None}
    )


def riders_of(graph: SceneGraph, carrier: SceneNode) -> list[SceneNode]:
    """What sits on the carrier's top: a register on a counter, a laptop on a desk."""
    top, shape = top_of(carrier), footprint(carrier)
    return [
        node
        for node in graph.nodes
        if node.id != carrier.id
        and not bounds_the_room(node)
        and abs(underside(node) - top) <= RIDING_GAP
        and contains_point(shape, (node.transform.position.x, node.transform.position.y))
    ]


def _riding(carrier: SceneNode, rider: SceneNode, move: NodeMove) -> NodeMove:
    """The rider's share of the carrier's move: it turns about the carrier's centre, not its own."""
    centre, at = carrier.transform.position, rider.transform.position
    radians = math.radians(move.delta_rotation_z_degrees)
    offset_x, offset_y = at.x - centre.x, at.y - centre.y
    turned_x = offset_x * math.cos(radians) - offset_y * math.sin(radians)
    turned_y = offset_x * math.sin(radians) + offset_y * math.cos(radians)
    return NodeMove(
        node_id=rider.id,
        delta_translation=Vec3(
            x=move.delta_translation.x + turned_x - offset_x,
            y=move.delta_translation.y + turned_y - offset_y,
            z=0.0,
        ),
        delta_rotation_z_degrees=move.delta_rotation_z_degrees,
    )


def carried_along(graph: SceneGraph, moves: list[NodeMove]) -> list[NodeMove]:
    """The moves plus one for everything sitting on a moved piece, so the things
    on a counter go where the counter goes. A rider the owner moved on its own
    keeps that move."""
    moved = {move.node_id for move in moves}
    riding: list[NodeMove] = []
    for move in moves:
        carrier = graph.by_id(move.node_id)
        for rider in riders_of(graph, carrier):
            if rider.id not in moved:
                moved.add(rider.id)
                riding.append(_riding(carrier, rider, move))
    return [*moves, *riding]


def without(graph: SceneGraph, node_ids) -> SceneGraph:
    """A layout with something set aside, for testing a relaxation only.

    This is never a proposal. Inventory is not adjustable, so removing a chair
    is a question for the owner and this exists to find out whether asking it
    would even help.
    """
    removed = set(node_ids)
    return graph.model_copy(
        update={"nodes": [n for n in graph.nodes if n.id not in removed]}
    )


def unlocked(graph: SceneGraph, node_ids) -> SceneGraph:
    """A layout where something fixed became movable, for testing a relaxation."""
    targets = set(node_ids)
    return graph.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"movable": True})
                if node.id in targets
                else node
                for node in graph.nodes
            ]
        }
    )


def footprint_span(node: SceneNode, axis: tuple[float, float]) -> float:
    """How far the node reaches along `axis`, from its centre."""
    centre = node.transform.position
    return max(
        abs((x - centre.x) * axis[0] + (y - centre.y) * axis[1])
        for x, y in footprint(node)
    )
