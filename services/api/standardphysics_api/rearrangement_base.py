"""The layout a rearrangement starts from: a stored revision plus what its scan knows.

A revision saved before the floor coverage was measured lacks
`floor_coverage`, without anything being wrong with it. It is recovered here
from the scan's own history when a revision is loaded to be dragged, asked
about, fixed or simulated. Nothing stored is rewritten; the next revision
saved from the result carries it.
"""

from __future__ import annotations

import json
import sqlite3

from standardphysics_contracts import FloorCoverage, SceneGraph

from . import repository as repo


def rearrangement_base(connection: sqlite3.Connection, row: sqlite3.Row) -> SceneGraph:
    """The stored revision, with floor coverage filled in from its history."""
    return _with_captured_coverage(connection, repo.graph_of(row))


def _with_captured_coverage(connection: sqlite3.Connection, graph: SceneGraph) -> SceneGraph:
    """The coverage revision 0 measured, for a later revision saved before it was.

    A grid is tied to its floor by id and follows that floor's transform, so
    it holds for any later revision that still has the floor, however the room
    was placed since.
    """
    if graph.floor_coverage or graph.revision == 0:
        return graph
    captured = repo.get_revision(connection, graph.scan_id, 0)
    if captured is None:
        return graph
    floors = {str(node.id) for node in graph.nodes}
    coverage = [
        FloorCoverage.model_validate(entry)
        for entry in json.loads(captured["graph_json"]).get("floor_coverage", [])
        if entry["floor_id"] in floors
    ]
    return graph.model_copy(update={"floor_coverage": coverage}) if coverage else graph
