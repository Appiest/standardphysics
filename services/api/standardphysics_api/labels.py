"""The owner names what a scan cannot: which object is the counter customers order at.

RoomPlan has no counter category and Astra labelling has not shipped, so on a
real scan no object is a service counter and the counter checks have nothing to
measure. Marking one relabels that node in a new revision, locks it in place,
and checks the shop again.
"""

from __future__ import annotations

import uuid

from standardphysics_contracts import SceneGraph, SceneNode, bounds_the_room

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
