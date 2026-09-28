"""Combining several rooms into one by hand, each dragged as one rigid group.

The owner aligns the scans in the workspace and saves their placements. A
placement moves every node in a room the same way: rotate about the room's
centroid, then slide it on the floor. The turn is about the vertical and is
applied to each node's whole rotation, because not every surface stands upright
in its own frame: RoomPlan lays a floor flat by tilting it, and rebuilding that
from its heading alone stood every floor on its edge.

The math here mirrors the web's `lib/room-groups.ts` exactly, so the preview
before saving and the graph after saving agree to the float.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Callable

import numpy as np
from pydantic import BaseModel
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, SurfaceAttachment, Vec3
from standardphysics_pipeline.ingest import parse_room_json
from standardphysics_pipeline.registration import PlaneAlignment, align_points

from . import repository as repo
from .db import Database
from .errors import ApiProblem
from .store import ArtifactStore
from .worker import ASSESS, Worker

POSITION = (3, 7, 11)


def _compose(
    m: list[float],
    centroid_x: float,
    centroid_y: float,
    yaw: float,
    tx: float,
    ty: float,
) -> list[float]:
    """A node transform after the room rotates `yaw` about its centroid and shifts by (tx, ty).

    Every column of the rotation turns with the room, and the vertical row is
    left alone, so whatever lay flat still lies flat.
    """
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    px, py = m[POSITION[0]] - centroid_x, m[POSITION[1]] - centroid_y
    x_row = [cos_y * m[column] - sin_y * m[4 + column] for column in range(3)]
    y_row = [sin_y * m[column] + cos_y * m[4 + column] for column in range(3)]
    return [
        *x_row, centroid_x + px * cos_y - py * sin_y + tx,
        *y_row, centroid_y + px * sin_y + py * cos_y + ty,
        m[8], m[9], m[10], m[POSITION[2]],
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


def _moved_with_room(m: list[float], room: RoomPlacement) -> list[float]:
    return _compose(m, room.cx, room.cy, math.radians(room.yaw_degrees), room.tx, room.ty)


def _placed(node: SceneNode, room: RoomPlacement) -> SceneNode:
    """The node carried with its room, along with where the scan found it.

    Placing a room re-registers the whole scan rather than rearranging it, so
    the position a rearrangement is measured from travels with the room.
    """
    transform = Mat4(m=_moved_with_room(node.transform.m, room))
    origin = node.measured_position
    if origin is None:
        return node.model_copy(update={"transform": transform})
    carried = _moved_with_room(Mat4.translation(origin.x, origin.y, origin.z).m, room)
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
        if repo.latest_revision_number(connection, scan_id) != body.base_revision:
            raise ApiProblem(409, "a newer layout was saved since this one started")
        repo.save_revision(connection, saved, source="owner", base_revision=body.base_revision)
        repo.enqueue_job(connection, scan_id, ASSESS, saved.revision)
    worker.wake()
    return saved


PLACEMENT_TOLERANCE_M = 0.05
"""How far a placed box may sit from the motion fitted to all of a walk's boxes.

Every placement moves each box of a walk by exactly one motion, so the residual
is arithmetic, not measurement. The tolerance only has to absorb rounding.
"""


def placement_since_capture(capture: SceneGraph, placed: SceneGraph, node_ids: list[str]) -> PlaneAlignment:
    """The motion from where the phone measured a walk to where its boxes stand now.

    The merged scan's first revision already carries the offset that spread the
    walks apart to be dragged, and every save adds a placement on top. Anything
    still in the capture's own frame, the LiDAR, the cameras and the photographed
    models, has seen none of that, so it needs the whole way across in one motion.

    Boxes pair by position in the list, which is the order they were copied in.
    The fit refuses with `AmbiguousRegistration` when they disagree on one motion.
    """
    end = {str(node.id): node for node in placed.nodes}
    pairs = [(node, end[node_id]) for node, node_id in zip(capture.nodes, node_ids) if node_id in end]
    source = [(a.transform.m[3], a.transform.m[7]) for a, _ in pairs]
    target = [(b.transform.m[3], b.transform.m[7]) for _, b in pairs]
    return align_points(source, target, tolerance=PLACEMENT_TOLERANCE_M)


def placement_matrix(placement: PlaneAlignment) -> np.ndarray:
    """Row-major 4x4 in the room frame: a walk as measured to the walk as placed."""
    cos, sin = np.cos(placement.yaw), np.sin(placement.yaw)
    tx, ty = placement.translation
    return np.array([[cos, -sin, 0, tx], [sin, cos, 0, ty], [0, 0, 1, 0], [0, 0, 0, 1]])


def captured_graph(store: ArtifactStore, scan_id: uuid.UUID) -> SceneGraph:
    """A walk's boxes as its phone measured them, in the capture's own frame."""
    return parse_room_json(json.loads(store.artifact_path(scan_id, "room-json").read_text()))


def _capture_pose(store: ArtifactStore, room: dict, placed: SceneGraph) -> dict | None:
    if not room.get("source_scan_id"):
        return None
    try:
        capture = captured_graph(store, uuid.UUID(room["source_scan_id"]))
        motion = placement_since_capture(capture, placed, room["node_ids"])
    except (OSError, ValueError):
        return None
    return {"yaw_degrees": math.degrees(motion.yaw), "tx": motion.translation[0], "ty": motion.translation[1]}


def rooms_of(database: Database, store: ArtifactStore, scan_id: uuid.UUID, revision: int | None) -> dict:
    """The combined scan's walks, each with where its capture frame stands in this revision."""
    manifest = store.scan_dir(scan_id) / "rooms.json"
    if not manifest.exists():
        return {"rooms": []}
    rooms = json.loads(manifest.read_text())["rooms"]
    with database.connect() as connection:
        row = repo.get_revision(connection, scan_id, revision)
    if row is None:
        return {"rooms": rooms}
    placed = repo.graph_of(row)
    return {"rooms": [{**room, "capture_pose": _capture_pose(store, room, placed)} for room in rooms]}


def found_since_capture(walk: SceneGraph, capture: SceneGraph) -> list[SceneNode]:
    """What a walk's own processing added to what its phone measured.

    RoomPlan boxes the furniture categories Apple ships. Discovery, run on the
    walk's photos after upload, adds everything else: the counter, the standing
    whiteboards, the security gates. A floor joined from RoomPlan's boxes alone
    has none of them.
    """
    captured = {node.id for node in capture.nodes}
    return [node for node in walk.nodes if node.id not in captured]


def carried_onto_floor(
    found: list[SceneNode],
    motion: np.ndarray,
    placed_ids: dict[uuid.UUID, uuid.UUID],
    renumbered: Callable[[str], str],
) -> list[SceneNode]:
    """A walk's findings moved by the walk's placement, pointing at the floor's own boxes and photos.

    `motion` is the row-major room-frame motion from where the walk was
    measured to where it was placed. `placed_ids` names each RoomPlan box of
    the walk by its id on the floor, and `renumbered` gives each photo its
    floor-wide number.
    """
    carried_ids = {node.id for node in found}

    def on_floor(node_id: uuid.UUID | None) -> uuid.UUID | None:
        if node_id is None or node_id in carried_ids:
            return node_id
        return placed_ids.get(node_id)

    return [_carried(node, motion, on_floor, renumbered) for node in found]


def _carried(
    node: SceneNode,
    motion: np.ndarray,
    on_floor: Callable[[uuid.UUID | None], uuid.UUID | None],
    renumbered: Callable[[str], str],
) -> SceneNode:
    parent = on_floor(node.parent_id)
    placed = motion @ np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
    update: dict = {
        "transform": Mat4(m=placed.reshape(16).tolist()),
        "parent_id": parent,
        "relation": node.relation if parent is not None else None,
        "texts": [
            text.model_copy(update={"evidence_frame_ids": [renumbered(one) for one in text.evidence_frame_ids]})
            for text in node.texts
        ],
    }
    if node.reconstruction is not None:
        update["reconstruction"] = node.reconstruction.model_copy(
            update={"evidence_frame_ids": [renumbered(one) for one in node.reconstruction.evidence_frame_ids]}
        )
    if node.attachment is not None:
        update["attachment"] = _carried_attachment(node.attachment, motion, on_floor, renumbered)
    return node.model_copy(update=update)


def _carried_attachment(
    attachment: SurfaceAttachment,
    motion: np.ndarray,
    on_floor: Callable[[uuid.UUID | None], uuid.UUID | None],
    renumbered: Callable[[str], str],
) -> SurfaceAttachment:
    """The mounting moved with its walk. The anchor is in its support's own frame, so it stays put."""
    turn = motion[:3, :3]

    def point(value: Vec3) -> Vec3:
        return _vec(turn @ np.asarray(value.as_tuple()) + motion[:3, 3])

    return attachment.model_copy(update={
        "support_node_id": on_floor(attachment.support_node_id),
        "normal": _vec(turn @ np.asarray(attachment.normal.as_tuple())) if attachment.normal is not None else None,
        "observed_region": [point(corner) for corner in attachment.observed_region],
        "observations": [
            crop.model_copy(update={"frame_id": renumbered(crop.frame_id)}) for crop in attachment.observations
        ],
    })


def _vec(values: np.ndarray) -> Vec3:
    return Vec3(x=float(values[0]), y=float(values[1]), z=float(values[2]))
