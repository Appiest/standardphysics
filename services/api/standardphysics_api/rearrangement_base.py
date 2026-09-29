"""The layout a rearrangement starts from: a stored revision plus what its scan knows.

Two things the hard constraints read can be missing from a stored revision
without anything being wrong with it. A revision saved before pieces recorded
where the scan found them lacks `measured_position` on the pieces it moved,
and a revision saved before the floor coverage was measured lacks
`floor_coverage`. Both are recovered here from the scan's own history when a
revision is loaded to be dragged, asked about, fixed or simulated. Nothing
stored is rewritten; the next revision saved from the result carries both.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid

from standardphysics_contracts import FloorCoverage, SceneGraph

from . import repository_revisions as revisions_repo
from .measured_origins import Origins, RevisionPoses, recover_origins, with_origins

log = logging.getLogger(__name__)

_RECOVERED: dict[tuple, Origins] = {}
"""Recovered origins by scan and the exact history they came from.

Every drag re-checks a layout, and walking a long history means parsing every
revision in it. The key names each revision's number, hash and save time, so
a new revision or an ingest that rewrote revision 0 is a different key.
"""

MOST_REMEMBERED = 32


def rearrangement_base(connection: sqlite3.Connection, row: sqlite3.Row) -> SceneGraph:
    """The stored revision, with origins and floor coverage filled in from its history."""
    graph = revisions_repo.graph_of(row)
    graph = _with_captured_coverage(connection, graph)
    recovered, unrecovered = with_origins(graph, _origins(connection, graph.scan_id, graph.revision))
    if unrecovered:
        log.info(
            "%s revision %s: %d moved pieces have no recoverable scan position",
            graph.scan_id, graph.revision, unrecovered,
        )
    return recovered


def _with_captured_coverage(connection: sqlite3.Connection, graph: SceneGraph) -> SceneGraph:
    """The coverage revision 0 measured, for a later revision saved before it was.

    A grid is tied to its floor by id and follows that floor's transform, so
    it holds for any later revision that still has the floor, however the room
    was placed since.
    """
    if graph.floor_coverage or graph.revision == 0:
        return graph
    captured = revisions_repo.get_revision(connection, graph.scan_id, 0)
    if captured is None:
        return graph
    floors = {str(node.id) for node in graph.nodes}
    coverage = [
        FloorCoverage.model_validate(entry)
        for entry in json.loads(captured["graph_json"]).get("floor_coverage", [])
        if entry["floor_id"] in floors
    ]
    return graph.model_copy(update={"floor_coverage": coverage}) if coverage else graph


def _origins(connection: sqlite3.Connection, scan_id: uuid.UUID, revision: int) -> Origins:
    history = connection.execute(
        "SELECT revision, graph_hash, created_at FROM revisions WHERE scan_id = ? AND revision <= ?"
        " ORDER BY revision",
        (str(scan_id), revision),
    ).fetchall()
    key = (str(scan_id), *((row["revision"], row["graph_hash"], row["created_at"]) for row in history))
    if key not in _RECOVERED:
        if len(_RECOVERED) >= MOST_REMEMBERED:
            _RECOVERED.clear()
        _RECOVERED[key] = recover_origins(_history(connection, scan_id, revision))
    return _RECOVERED[key]


def _history(connection: sqlite3.Connection, scan_id: uuid.UUID, revision: int) -> list[RevisionPoses]:
    rows = connection.execute(
        "SELECT revision, created_at, graph_json FROM revisions WHERE scan_id = ? AND revision <= ?"
        " ORDER BY revision",
        (str(scan_id), revision),
    ).fetchall()
    return [RevisionPoses.of(row["revision"], row["created_at"], json.loads(row["graph_json"])) for row in rows]
