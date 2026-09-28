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
from standardphysics_contracts.scene import bounds_the_room
from standardphysics_pipeline.ingest import parse_room_json
from standardphysics_pipeline.registration import PlaneAlignment, align_points
from standardphysics_pipeline.substrate.regions import Region, bounds, overlap_fraction, region_of, volume

from . import repository as repo
from .budgets import admit_new_job
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
        admit_new_job(connection, worker.settings.max_queued_jobs)
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


def walk_on_floor(
    walk: SceneGraph,
    capture: SceneGraph,
    placed: SceneGraph,
    motion: np.ndarray,
    placed_ids: dict[uuid.UUID, uuid.UUID],
    renumbered: Callable[[str], str],
) -> list[SceneNode]:
    """A walk as its own processing left it, moved to where the walk was placed.

    Processing takes boxes away and renames them as well as adding them. On
    Moffitt's four walks it removed 110 of the boxes the phone measured, mostly
    chairs and sofas read off built-in seating that discovery measured again,
    and renamed 29. A floor built from the phone's boxes put every one back.

    `placed_ids` names each box the phone measured by its id on the floor, so
    anything resting on it still finds it; a box processing added keeps its own
    id. A box the phone measured and processing left the same shape stays
    exactly where the owner placed it on the floor rather than where the fitted
    `motion` puts it, which differs by a fraction of a millimetre, because the
    floor's painted surface was baked from that placement.
    """
    measured = {node.id: node for node in capture.nodes}
    where_placed = {node.id: node for node in placed.nodes}

    def on_floor(node_id: uuid.UUID | None) -> uuid.UUID | None:
        return None if node_id is None else placed_ids.get(node_id, node_id)

    def carried(node: SceneNode) -> SceneNode:
        moved = _carried(node, motion, on_floor, renumbered).model_copy(update={"id": on_floor(node.id)})
        as_measured, as_placed = measured.get(node.id), where_placed.get(moved.id)
        if as_measured is None or as_placed is None or not _same_shape_and_place(node, as_measured):
            return moved
        return moved.model_copy(update={"transform": as_placed.transform})

    return [carried(node) for node in walk.nodes]


def _same_shape_and_place(a: SceneNode, b: SceneNode) -> bool:
    return np.allclose(a.transform.m, b.transform.m) and np.allclose(a.dimensions.as_tuple(), b.dimensions.as_tuple())


SAME_THING = 0.5
"""How much of each of two boxes must lie inside the other for them to be one
thing seen twice. A bag on a table, or a cup inside a sofa's box, lies within
the other box, but the other box does not lie within it."""

QUALITY_RANK = {"confirmed": 0, "measured": 1, "needs_another_look": 2}


def without_repeats_across_walks(walks: list[list[SceneNode]]) -> list[SceneNode]:
    """Every walk's nodes, with a thing that two overlapping walks both boxed kept once.

    Walks of one floor overlap where they meet, so the same bench can be boxed
    by each walk that passed it, sometimes under different names. Of two such
    boxes the better measured is kept, then the larger, and whatever rested on
    the other rests on it instead. Boxes within one walk are that walk's own
    business, and the room's sheets are never merged.
    """
    tagged = [(index, node) for index, nodes in enumerate(walks) for node in nodes]
    solids = sorted(
        ((index, node, region_of(node)) for index, node in tagged if not bounds_the_room(node)),
        key=lambda item: (QUALITY_RANK.get(item[1].quality, len(QUALITY_RANK)), -volume(item[2])),
    )
    kept: list[tuple[int, Region]] = []
    replaced: dict[uuid.UUID, uuid.UUID] = {}
    for index, node, region in solids:
        repeat_of = next((other for walk, other in kept if walk != index and _same_thing(region, other)), None)
        if repeat_of is None:
            kept.append((index, region))
        else:
            replaced[node.id] = repeat_of.id
    return [_pointed_past(node, replaced) for _, node in tagged if node.id not in replaced]


def _same_thing(a: Region, b: Region) -> bool:
    (a_low, a_high), (b_low, b_high) = bounds(a), bounds(b)
    if np.any(a_high < b_low) or np.any(b_high < a_low):
        return False
    return min(overlap_fraction(a, b), overlap_fraction(b, a)) >= SAME_THING


def _pointed_past(node: SceneNode, replaced: dict[uuid.UUID, uuid.UUID]) -> SceneNode:
    """The node, resting on and mounted to the kept box wherever it named a dropped repeat."""
    update: dict = {}
    if node.parent_id in replaced:
        update["parent_id"] = replaced[node.parent_id]
    if node.attachment is not None and node.attachment.support_node_id in replaced:
        update["attachment"] = node.attachment.model_copy(
            update={"support_node_id": replaced[node.attachment.support_node_id]}
        )
    return node.model_copy(update=update) if update else node


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
    if node.measured_position is not None:
        update["measured_position"] = _vec((motion @ np.append(node.measured_position.as_tuple(), 1.0))[:3])
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
