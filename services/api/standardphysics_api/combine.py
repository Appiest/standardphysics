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
from standardphysics_contracts import Mat4, SceneGraph

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
    moved: dict[uuid.UUID, list[float]] = {}
    for room in rooms:
        yaw = math.radians(room.yaw_degrees)
        for node_id in room.node_ids:
            node = next(node for node in graph.nodes if node.id == node_id)
            moved[node_id] = _compose(node.transform.m, room.cx, room.cy, yaw, room.tx, room.ty)
    nodes = [
        node.model_copy(update={"transform": Mat4(m=moved[node.id])}) if node.id in moved else node
        for node in graph.nodes
    ]
    return graph.model_copy(update={"nodes": nodes})


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
