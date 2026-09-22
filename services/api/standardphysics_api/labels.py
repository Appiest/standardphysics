"""The owner names what a scan cannot: which object is the counter customers order at.

RoomPlan has no counter category and Astra labelling has not shipped, so on a
real scan no object is a service counter and the counter checks have nothing to
measure. Marking one relabels that node in a new revision, locks it in place,
and checks the shop again.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from PIL import Image
from standardphysics_contracts import (
    ManualMarkRequest,
    ObservationCrop,
    SceneGraph,
    SceneNode,
    SurfaceAttachment,
    UnlocalizedObservation,
    bounds_the_room,
)
from standardphysics_contracts.textures import FRAME_ID_PATTERN
from standardphysics_pipeline.discovery.crops import save_crop

from . import repository as repo
from .db import Database
from .errors import ApiProblem
from .layout import STALE_LAYOUT
from .worker import ASSESS, Worker

SERVICE_COUNTER_LABEL = "service counter"


def mark_counter(
    database: Database, worker: Worker, scan_id: uuid.UUID, base_revision: int, node_id: uuid.UUID
) -> SceneGraph:
    return _relabel(database, worker, scan_id, base_revision, node_id, _as_counter)


def unmark_counter(
    database: Database, worker: Worker, scan_id: uuid.UUID, base_revision: int, node_id: uuid.UUID
) -> SceneGraph:
    return _relabel(database, worker, scan_id, base_revision, node_id, _as_scanned)


def review_outlet(
    database: Database, worker: Worker, scan_id: uuid.UUID, base_revision: int, node_id: uuid.UUID, status: str
) -> SceneGraph:
    if status not in ("confirmed_by_user", "rejected_by_user", "detected", "candidate"):
        raise ApiProblem(400, "invalid review status")
    base = _base_graph(database, scan_id, base_revision)
    try:
        target = base.by_id(node_id)
    except KeyError:
        raise ApiProblem(404, "no such outlet") from None

    if target.kind not in ("outlet", "candidate_outlet") and target.attachment is None:
        raise ApiProblem(400, "only outlets can be reviewed")

    def _update(node: SceneNode) -> SceneNode:
        if node.id != target.id:
            return node
        att = node.attachment
        if att is not None:
            att = att.model_copy(update={"review_status": status})
        return node.model_copy(update={"attachment": att, "labeled_by": "owner"})

    nodes = [_update(node) for node in base.nodes]
    saved = base.model_copy(update={"nodes": nodes, "revision": base_revision + 1})
    with database.transaction() as connection:
        if repo.get_revision(connection, scan_id)["revision"] != base_revision:
            raise ApiProblem(409, STALE_LAYOUT)
        repo.save_revision(connection, saved, source="owner", base_revision=base_revision)
        repo.enqueue_job(connection, scan_id, ASSESS, saved.revision)
    worker.wake()
    return saved


def mark_observation(
    database: Database,
    store,
    worker: Worker,
    scan_id: uuid.UUID,
    base_revision: int,
    body: ManualMarkRequest,
    actor_email: str,
) -> SceneGraph:
    """Store a person's photo mark for a target class, on a node or unlocalized.

    Never relabels the node: a mark says "this is evidenced here", and a role
    label only changes through the role endpoints. The actor and time ride with
    the crop so a manual mark can never pass as an automatic detection, and a
    real deterministic crop of the marked sensor region is saved so review can
    look at exactly what the person pointed at.
    """
    base = _base_graph(database, scan_id, base_revision)
    crop = _manual_crop(body, actor_email, image_url=_cut_crop(database, store, scan_id, body))
    crop_id = crop.image_url

    if body.node_id is not None:
        try:
            target = base.by_id(body.node_id)
        except KeyError:
            raise ApiProblem(404, "no such object") from None
        current = target.attachment or SurfaceAttachment(support_type="unanchored")
        updated = current.model_copy(update={
            "observations": [*current.observations, crop],
            "review_status": body.review_status,
            "uncertainty_reasons": list(dict.fromkeys([*current.uncertainty_reasons, "manual photo mark"])),
        })
        nodes = [
            node.model_copy(update={"attachment": updated, "labeled_by": "owner"})
            if node.id == target.id else node
            for node in base.nodes
        ]
        saved = base.model_copy(update={"nodes": nodes, "revision": base_revision + 1})
    else:
        observation = UnlocalizedObservation(
            id=uuid.uuid4(),
            target_class=body.target_class,
            frame_id=body.frame_id,
            sensor_box=body.sensor_box,
            provenance="manual",
            marked_by=actor_email,
            marked_at=crop.marked_at,
            note=body.note,
            review_status=body.review_status,
            image_url=crop_id,
        )
        saved = base.model_copy(update={
            "unlocalized_observations": [*base.unlocalized_observations, observation],
            "revision": base_revision + 1,
        })

    with database.transaction() as connection:
        if repo.get_revision(connection, scan_id)["revision"] != base_revision:
            raise ApiProblem(409, STALE_LAYOUT)
        repo.save_revision(connection, saved, source="owner", base_revision=base_revision)
        repo.enqueue_job(connection, scan_id, ASSESS, saved.revision)
    worker.wake()
    return saved


def _cut_crop(database: Database, store, scan_id: uuid.UUID, body: ManualMarkRequest) -> str | None:
    """Cut the deterministic source-resolution crop of the marked box, or None.

    The frame must be a REGISTERED frame-kind artifact whose bytes decode, and
    the box must lie inside the stored sensor pixels. Anything else is a clear
    400: a mark without resolvable pixels must never look like a mark with
    them, and an out-of-image box must never be persisted while the crop is
    silently clamped elsewhere.
    """
    if not re.fullmatch(FRAME_ID_PATTERN, body.frame_id):
        raise ApiProblem(400, "frame not stored for this mark")
    with database.connect() as connection:
        artifact = repo.find_artifact(connection, scan_id, body.frame_id)
    if artifact is None or artifact.kind != "frames":
        raise ApiProblem(400, "frame not stored for this mark")
    frame_path = store.artifact_path(scan_id, body.frame_id)
    try:
        with Image.open(frame_path) as opened:
            width, height = opened.size
    except (OSError, ValueError):
        raise ApiProblem(400, "frame image cannot be read") from None
    left, top, right, bottom = body.sensor_box
    if not (right > left and bottom > top):
        raise ApiProblem(400, "sensor box does not describe a usable image region")
    if left < 0 or top < 0 or right > width or bottom > height:
        raise ApiProblem(400, "sensor box lies outside the stored frame")
    crop_dir = store.scan_dir(scan_id) / "crops"
    crop_id = save_crop(frame_path, body.frame_id, (left, top, right, bottom), crop_dir)
    if crop_id is None:
        raise ApiProblem(400, "sensor box does not describe a usable image region")
    return crop_id


def _manual_crop(body: ManualMarkRequest, actor_email: str, image_url: str | None = None) -> ObservationCrop:
    return ObservationCrop(
        frame_id=body.frame_id,
        sensor_box=body.sensor_box,
        confidence=1.0,
        provenance="manual",
        marked_by=actor_email,
        marked_at=datetime.now(UTC),
        note=body.note,
        image_url=image_url,
    )



def _as_counter(node: SceneNode) -> SceneNode:
    return node.model_copy(update={"label": SERVICE_COUNTER_LABEL, "labeled_by": "owner", "movable": False})


def _as_scanned(node: SceneNode) -> SceneNode:
    return node.model_copy(update={"label": node.raw_category, "labeled_by": "owner"})


def _relabel(database, worker, scan_id, base_revision, node_id, change) -> SceneGraph:
    base = _base_graph(database, scan_id, base_revision)
    target = _object_node(base, node_id)
    nodes = [change(node) if node.id == target.id else node for node in base.nodes]
    saved = base.model_copy(update={"nodes": nodes, "revision": base_revision + 1})
    with database.transaction() as connection:
        if repo.get_revision(connection, scan_id)["revision"] != base_revision:
            raise ApiProblem(409, STALE_LAYOUT)
        repo.save_revision(connection, saved, source="owner", base_revision=base_revision)
        repo.enqueue_job(connection, scan_id, ASSESS, saved.revision)
    worker.wake()
    return saved


def _base_graph(database: Database, scan_id: uuid.UUID, base_revision: int) -> SceneGraph:
    with database.connect() as connection:
        if not repo.scan_exists(connection, scan_id):
            raise ApiProblem(404, "no scan")
        row = repo.get_revision(connection, scan_id, base_revision)
    if row is None:
        raise ApiProblem(404, "no such revision")
    return repo.graph_of(row)


def _object_node(graph: SceneGraph, node_id: uuid.UUID) -> SceneNode:
    try:
        node = graph.by_id(node_id)
    except KeyError:
        raise ApiProblem(404, "no such object") from None
    if bounds_the_room(node):
        raise ApiProblem(400, "only furniture and fixtures can be a counter")
    return node
