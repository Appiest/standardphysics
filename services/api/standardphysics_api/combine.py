"""Combining several rooms into one by hand, each dragged as one rigid group.

The owner aligns the scans in the workspace and saves their placements. A
placement moves every node in a room the same way: rotate about the room's
centroid, then slide it on the floor. Nodes stay a pure rotation about the
vertical axis plus a translation, which is all a RoomPlan surface carries.

The math here mirrors the web's `lib/room-groups.ts` exactly, so the preview
before saving and the graph after saving agree to the float.
"""

from __future__ import annotations

import math
import uuid

from pydantic import BaseModel
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3

from . import repository as repo
from .db import Database
from .errors import ApiProblem
from .worker import ASSESS, Worker

FORWARD_AXIS = (0, 4)
POSITION = (3, 7, 11)


def _compose(
    m: list[float],
    centroid_x: float,
    centroid_y: float,
    yaw: float,
    tx: float,
    ty: float,
) -> list[float]:
    """A node transform after the room rotates `yaw` about its centroid and shifts by (tx, ty)."""
    angle = math.atan2(m[FORWARD_AXIS[1]], m[FORWARD_AXIS[0]]) + yaw
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    px, py = m[POSITION[0]] - centroid_x, m[POSITION[1]] - centroid_y
    rx = px * cos_y - py * sin_y
    ry = px * sin_y + py * cos_y
    return [
        cos_a, -sin_a, 0.0, centroid_x + rx + tx,
        sin_a, cos_a, 0.0, centroid_y + ry + ty,
        0.0, 0.0, 1.0, m[POSITION[2]],
        0.0, 0.0, 0.0, 1.0,
    ]


class RoomPlacement(BaseModel):
    node_ids: list[uuid.UUID]
    yaw_degrees: float
    tx: float
    ty: float
    cx: float
    cy: float


class SaveCombineRequest(BaseModel):
    base_revision: int
    rooms: list[RoomPlacement]


def apply_room_placements(graph: SceneGraph, rooms: list[RoomPlacement]) -> SceneGraph:
    """The same graph with every listed node moved by its room's placement."""
    known = {node.id for node in graph.nodes}
    for room in rooms:
        unknown = [str(node_id) for node_id in room.node_ids if node_id not in known]
        if unknown:
            raise ApiProblem(400, "unknown node", need=unknown)
    placements = {node_id: room for room in rooms for node_id in room.node_ids}
    nodes = [
        _placed(node, placements[node.id]) if node.id in placements else node
        for node in graph.nodes
    ]
    return graph.model_copy(update={"nodes": nodes})


def _carried(m: list[float], room: RoomPlacement) -> list[float]:
    return _compose(m, room.cx, room.cy, math.radians(room.yaw_degrees), room.tx, room.ty)


def _placed(node: SceneNode, room: RoomPlacement) -> SceneNode:
    """The node carried with its room, along with where the scan found it.

    Placing a room re-registers the whole scan rather than rearranging it, so
    the position a rearrangement is measured from travels with the room.
    """
    transform = Mat4(m=_carried(node.transform.m, room))
    origin = node.measured_position
    if origin is None:
        return node.model_copy(update={"transform": transform})
    carried = _carried(Mat4.translation(origin.x, origin.y, origin.z).m, room)
    return node.model_copy(update={
        "transform": transform,
        "measured_position": Vec3(x=carried[POSITION[0]], y=carried[POSITION[1]], z=origin.z),
    })


def save_combine(database: Database, worker: Worker, scan_id: uuid.UUID, body: SaveCombineRequest) -> SceneGraph:
    with database.connect() as connection:
        if not repo.scan_exists(connection, scan_id):
            raise ApiProblem(404, "no scan")
        row = repo.get_revision(connection, scan_id, body.base_revision)
    if row is None:
        raise ApiProblem(404, "no such revision")
    base = repo.graph_of(row)
    combined = apply_room_placements(base, body.rooms)
    saved = combined.model_copy(update={"revision": body.base_revision + 1})
    with database.transaction() as connection:
        if repo.get_revision(connection, scan_id)["revision"] != body.base_revision:
            raise ApiProblem(409, "a newer layout was saved since this one started")
        repo.save_revision(connection, saved, source="owner", base_revision=body.base_revision)
        repo.enqueue_job(connection, scan_id, ASSESS, saved.revision)
    worker.wake()
    return saved
