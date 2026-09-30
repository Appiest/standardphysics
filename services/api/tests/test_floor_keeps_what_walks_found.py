"""A floor joined from several walks keeps what discovery found in each of them.

RoomPlan boxes the furniture categories Apple ships. The counter at a library's
entrance, the standing whiteboards and the security gates are found afterwards,
by discovery on each walk's own photos. A floor built from the walks' RoomPlan
boxes alone lost all of them, so none could be picked on the floor.

This runs against a real floor of Moffitt Library and its four walks, as they
sit in a development database, and skips where that database is absent.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import uuid

import numpy as np
import pytest
from standardphysics_contracts import SceneGraph

from standardphysics_api.combine import (
    captured_graph,
    placement_matrix,
    placement_since_capture,
    walk_on_floor,
)
from standardphysics_api.store import ArtifactStore

DATA = pathlib.Path(os.environ.get("SP_DATA_DIR", pathlib.Path(__file__).resolve().parents[1] / "var"))
FLOOR = "84548283-d462-4dc8-9d17-ff18e59a69cc"
FRAMES_PER_WALK = 10_000

pytestmark = pytest.mark.skipif(
    not (DATA / "scans" / FLOOR / "rooms.json").is_file(),
    reason="the Moffitt floor is not in this development database",
)


def latest(database: sqlite3.Connection, scan_id: str) -> SceneGraph:
    row = database.execute(
        "SELECT graph_json FROM revisions WHERE scan_id=? ORDER BY revision DESC LIMIT 1", (scan_id,)
    ).fetchone()
    return SceneGraph.model_validate_json(row[0])


def position(node) -> np.ndarray:
    return np.asarray([node.transform.m[3], node.transform.m[7], node.transform.m[11]])


@pytest.fixture(scope="module")
def walks():
    """Each walk's findings, carried onto the floor, beside what the walk itself says."""
    database = sqlite3.connect(DATA / "standardphysics.sqlite3")
    store = ArtifactStore(DATA, max_bytes=0)
    placed = latest(database, FLOOR)
    rooms = json.loads((DATA / "scans" / FLOOR / "rooms.json").read_text())["rooms"]
    carried = []
    for index, room in enumerate(rooms):
        scan_id = room["source_scan_id"]
        capture = captured_graph(store, uuid.UUID(scan_id))
        motion = placement_matrix(placement_since_capture(capture, placed, room["node_ids"]))
        placed_ids = {
            node.id: uuid.UUID(node_id) for node, node_id in zip(capture.nodes, room["node_ids"], strict=False)
        }
        first = (index + 1) * FRAMES_PER_WALK
        walk = latest(database, scan_id)
        measured = {node.id for node in capture.nodes}
        on_floor = walk_on_floor(
            walk, capture, placed, motion, placed_ids,
            lambda frame, first=first: f"frame-{first + int(frame.removeprefix('frame-')):05d}",
        )
        found = [node for node in walk.nodes if node.id not in measured]
        moved = [after for before, after in zip(walk.nodes, on_floor, strict=True) if before.id not in measured]
        carried.append({"walk": walk, "found": found, "moved": moved, "motion": motion, "first": first})
    return placed, carried


class TestTheFloorKeepsWhatEachWalkFound:
    def test_every_walk_brings_its_discovered_objects(self, walks):
        _, carried = walks
        for walk in carried:
            assert walk["found"] and len(walk["moved"]) == len(walk["found"])

    def test_each_object_lands_where_its_walk_was_placed(self, walks):
        _, carried = walks
        for walk in carried:
            for before, after in zip(walk["found"], walk["moved"], strict=True):
                expected = walk["motion"] @ np.append(position(before), 1.0)
                assert np.allclose(position(after), expected[:3], atol=1e-6)

    def test_something_resting_on_a_measured_box_still_rests_on_that_box(self, walks):
        placed, carried = walks
        on_floor = {node.id: node for node in placed.nodes}
        resting = 0
        for walk in carried:
            in_walk = {node.id: node for node in walk["walk"].nodes}
            for before, after in zip(walk["found"], walk["moved"], strict=True):
                if after.parent_id not in on_floor:
                    continue
                resting += 1
                apart_in_walk = np.linalg.norm(position(before) - position(in_walk[before.parent_id]))
                apart_on_floor = np.linalg.norm(position(after) - position(on_floor[after.parent_id]))
                assert apart_on_floor == pytest.approx(apart_in_walk, abs=0.01)
        assert resting > 0

    def test_photographic_evidence_points_at_the_floors_own_photos(self, walks):
        _, carried = walks
        crops = [
            (walk["first"], crop.frame_id)
            for walk in carried for node in walk["moved"] if node.attachment is not None
            for crop in node.attachment.observations
        ]
        assert crops
        for first, frame_id in crops:
            assert first <= int(frame_id.removeprefix("frame-")) < first + FRAMES_PER_WALK
