"""Construction edits: pushing one side of the room outward, or relocating a built-in fixture.

Some rooms cannot be cleared by furniture alone, because the turning circle or
the route does not fit between the pieces and the floor's edge. For those the
model may propose moving a wall. A shift grows the floor on one side, carries
the walls, doors and windows standing on that edge out with it, and stretches
the walls that run into that edge so the room stays closed. The checker then
remeasures the enlarged room exactly as it measures any other; nothing here
decides whether the shift helped.

Sides are named by the floor's own horizontal axes, because a scanned floor is
rarely square to the world: `x+` is the edge the floor's local x axis points
at, and `room_view` publishes each side's outward direction and segment.

A fixture move relocates something built in, such as a reception desk or a
partition wall, when it is what blocks a turning circle or a route. Only
fixtures qualify: furniture moves stay ordinary moves, and pieces the phantom
filter pins cannot be moved at all.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3, lies_flat, to_meters
from standardphysics_pipeline.occupancy import reads_as_wall

from ..checks.walls import is_room_shell

MAX_WALL_SHIFT_INCHES = 12.0
MAX_FIXTURE_MOVE_INCHES = 24.0
EDGE_TOLERANCE_METERS = 0.35
"""How far a wall's centre line may sit from the floor edge and still stand on it."""
PARALLEL_COSINE = 0.3

Side = Literal["x+", "x-", "y+", "y-"]
SIDES: tuple[Side, ...] = ("x+", "x-", "y+", "y-")


class WallShift(BaseModel):
    model_config = ConfigDict(extra="forbid")
    side: Side
    inches: float = Field(gt=0, le=MAX_WALL_SHIFT_INCHES)


class FixtureMove(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    node_id: UUID
    dx_inches: float = Field(ge=-MAX_FIXTURE_MOVE_INCHES, le=MAX_FIXTURE_MOVE_INCHES)
    dy_inches: float = Field(ge=-MAX_FIXTURE_MOVE_INCHES, le=MAX_FIXTURE_MOVE_INCHES)

    @property
    def inches(self) -> float:
        return math.hypot(self.dx_inches, self.dy_inches)


def fixture_ids(graph: SceneGraph) -> set[UUID]:
    """Built-in objects, and interior walls such as partitions; an exterior wall moves only by a wall shift."""
    edges = floor_edges(graph)

    def on_an_edge(node: SceneNode) -> bool:
        return any(abs(_offset(node, edge) - edge.half_extent) <= EDGE_TOLERANCE_METERS for edge in edges)

    return {node.id for node in graph.nodes if not node.movable and not lies_flat(node)
            and (not is_room_shell(node) or (reads_as_wall(node) and not on_an_edge(node)))}


@dataclass(frozen=True)
class FloorEdge:
    side: Side
    axis: int
    """Index of the floor's local axis this edge is normal to."""
    outward: tuple[float, float]
    half_extent: float
    centre: tuple[float, float]
    along: tuple[float, float]
    half_length: float

    def segment(self) -> list[float]:
        mid = (self.centre[0] + self.outward[0] * self.half_extent, self.centre[1] + self.outward[1] * self.half_extent)
        return [round(mid[0] - self.along[0] * self.half_length, 2), round(mid[1] - self.along[1] * self.half_length, 2),
                round(mid[0] + self.along[0] * self.half_length, 2), round(mid[1] + self.along[1] * self.half_length, 2)]


def _axis(node: SceneNode, index: int) -> tuple[float, float, float]:
    m = node.transform.m
    return (m[index], m[4 + index], m[8 + index])


def _unit(x: float, y: float) -> tuple[float, float]:
    length = math.hypot(x, y)
    return (x / length, y / length)


def _extent(node: SceneNode, index: int) -> float:
    return node.dimensions.as_tuple()[index]


def _horizontal_axes(node: SceneNode) -> list[int]:
    """The two local axes lying on the floor, in index order."""
    level = [index for index in range(3) if abs(_axis(node, index)[2]) < 0.5]
    return sorted(level, key=lambda index: -_extent(node, index))[:2] if len(level) > 2 else level


def floor_edges(graph: SceneGraph) -> list[FloorEdge]:
    """The four sides a construction edit may push, or none when the room has no single flat floor."""
    floor = graph.ground()
    if floor is None:
        return []
    axes = sorted(_horizontal_axes(floor))
    if len(axes) != 2:
        return []
    centre = (floor.transform.position.x, floor.transform.position.y)
    edges = []
    for name, index, other in (("x", axes[0], axes[1]), ("y", axes[1], axes[0])):
        direction = _unit(*_axis(floor, index)[:2])
        along = _unit(*_axis(floor, other)[:2])
        for sign in (1, -1):
            edges.append(FloorEdge(
                side=f"{name}{'+' if sign > 0 else '-'}", axis=index,  # type: ignore[arg-type]
                outward=(direction[0] * sign, direction[1] * sign), half_extent=_extent(floor, index) / 2,
                centre=centre, along=along, half_length=_extent(floor, other) / 2,
            ))
    return edges


def _translated(node: SceneNode, dx: float, dy: float) -> Mat4:
    m = list(node.transform.m)
    m[3] += dx
    m[7] += dy
    return Mat4(m=m)


def _grown(node: SceneNode, index: int, meters: float, dx: float, dy: float) -> SceneNode:
    size = list(node.dimensions.as_tuple())
    size[index] += meters
    return node.model_copy(update={"dimensions": Vec3(x=size[0], y=size[1], z=size[2]),
                                   "transform": _translated(node, dx, dy)})


def _long_axis(node: SceneNode) -> int:
    level = [index for index in range(3) if abs(_axis(node, index)[2]) < 0.5]
    return max(level, key=lambda index: _extent(node, index))


def _offset(node: SceneNode, edge: FloorEdge) -> float:
    """Signed distance of the node's centre from the floor centre, along the edge's outward direction."""
    position = node.transform.position
    return (position.x - edge.centre[0]) * edge.outward[0] + (position.y - edge.centre[1]) * edge.outward[1]


def _shifted_node(node: SceneNode, edge: FloorEdge, meters: float) -> SceneNode:
    dx, dy = edge.outward[0] * meters, edge.outward[1] * meters
    if lies_flat(node) or not is_room_shell(node):
        return node
    offset = _offset(node, edge)
    if reads_as_wall(node):
        long_axis = _long_axis(node)
        direction = _unit(*_axis(node, long_axis)[:2])
        cosine = abs(direction[0] * edge.outward[0] + direction[1] * edge.outward[1])
        if cosine > 1 - PARALLEL_COSINE and offset + _extent(node, long_axis) / 2 >= edge.half_extent - EDGE_TOLERANCE_METERS:
            return _grown(node, long_axis, meters, dx / 2, dy / 2)
        if cosine > PARALLEL_COSINE:
            return node
    if abs(offset - edge.half_extent) <= EDGE_TOLERANCE_METERS:
        return node.model_copy(update={"transform": _translated(node, dx, dy)})
    return node


def shift_walls(graph: SceneGraph, shifts: list[WallShift]) -> SceneGraph:
    """The room with each named side pushed outward; the original graph is never touched."""
    if not shifts:
        return graph
    for shift in shifts:
        edges = {edge.side: edge for edge in floor_edges(graph)}
        edge = edges.get(shift.side)
        if edge is None:
            raise ValueError(f"this room has no movable side {shift.side}")
        meters = to_meters(shift.inches)
        floor = graph.ground()
        assert floor is not None
        nodes = [
            _grown(node, edge.axis, meters, edge.outward[0] * meters / 2, edge.outward[1] * meters / 2)
            if node.id == floor.id else _shifted_node(node, edge, meters)
            for node in graph.nodes
        ]
        graph = graph.model_copy(update={"nodes": nodes, "revision": graph.revision + 1, "base_hash": None})
    return graph


def move_fixtures(graph: SceneGraph, moves: list[FixtureMove]) -> SceneGraph:
    """The room with each named fixture slid across the floor; the original graph is never touched."""
    if not moves:
        return graph
    by_id = {move.node_id: move for move in moves}
    fixtures = fixture_ids(graph)
    nodes = []
    for node in graph.nodes:
        move = by_id.get(node.id)
        if move is not None and node.id not in fixtures:
            raise ValueError(f"{node.label} is not a built-in fixture")
        nodes.append(node if move is None else node.model_copy(update={
            "transform": _translated(node, to_meters(move.dx_inches), to_meters(move.dy_inches))}))
    if len(by_id) != sum(1 for node in graph.nodes if node.id in by_id):
        raise ValueError("a fixture move names an object that is not in the room")
    return graph.model_copy(update={"nodes": nodes, "revision": graph.revision + 1, "base_hash": None})


def build(graph: SceneGraph, shifts: list[WallShift], fixtures: list[FixtureMove]) -> SceneGraph:
    """The room after all its construction: walls pushed out first, then fixtures relocated."""
    return move_fixtures(shift_walls(graph, shifts), fixtures)


def construction_inches(shifts: list[WallShift], fixtures: list[FixtureMove] = ()) -> float:
    return round(sum(shift.inches for shift in shifts) + sum(move.inches for move in fixtures), 2)
