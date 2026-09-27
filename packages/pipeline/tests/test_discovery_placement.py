"""Where discovered and scanned pieces stand, pinned against geometry with a known answer.

A café scan put all of these wrong at once: RoomPlan's counter lid an inch
high, the counter itself filed as storage, and its carved lid floating as a
second counter at chest height.
"""

from __future__ import annotations

import math
import uuid
from collections import Counter
from dataclasses import replace

import numpy as np
import pytest
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_pipeline.coords import capture_to_room
from standardphysics_pipeline.discovery.carve import CarvedBox, fit_box
from standardphysics_pipeline.discovery.detect import Detection
from standardphysics_pipeline.discovery.merge import DiscoveredObject
from standardphysics_pipeline.discovery.placement import part_of_a_scanned_piece, standing_on_the_floor
from standardphysics_pipeline.discovery.semantic_corrections import apply_secondary_semantic_corrections
from standardphysics_pipeline.discovery.worktops import measure_worktops, measured_top
from standardphysics_pipeline.textures.camera import PhotoCamera


def slab(centre, size, spacing=0.02) -> np.ndarray:
    ranges = [np.arange(c - s / 2, c + s / 2 + spacing / 2, spacing) for c, s in zip(centre, size)]
    grid = np.meshgrid(*ranges, indexing="ij")
    return np.stack([axis.ravel() for axis in grid], axis=1)


def piece(label, centre, size, *, yaw=0.0) -> SceneNode:
    cos_t, sin_t = math.cos(yaw), math.sin(yaw)
    return SceneNode(
        id=uuid.uuid4(), kind="object", label=label, raw_category=label.lower(),
        dimensions=Vec3(x=size[0], y=size[1], z=size[2]),
        transform=Mat4(m=[cos_t, -sin_t, 0.0, centre[0], sin_t, cos_t, 0.0, centre[1],
                          0.0, 0.0, 1.0, centre[2], 0.0, 0.0, 0.0, 1.0]),
    )


def graph_of(*nodes) -> SceneGraph:
    return SceneGraph(scan_id=uuid.uuid4(), nodes=list(nodes), capture_to_room=capture_to_room(0.0))


def carved(name, points) -> DiscoveredObject:
    box = fit_box(points)
    assert box is not None
    return DiscoveredObject(name=name, box=box, movable=True, confidence=0.9, frame_ids=("f0", "f1"),
                            weights=Counter({name: 1.8}))


def top(box: CarvedBox) -> float:
    return box.centre[2] + box.dimensions[2] / 2


COUNTER_TOP = 0.87
"""A 34.25 inch counter, measured; RoomPlan boxed it to 0.91."""


def scanned_counter(label="Counter") -> SceneNode:
    return piece(label, (0.0, 0.0, 0.455), (1.7, 0.7, 0.91))


def counter_surface() -> np.ndarray:
    return slab((0.0, 0.0, COUNTER_TOP), (1.7, 0.7, 0.0))


class TestReadingAWorktopOffTheMesh:
    def test_the_counter_top_is_where_the_mesh_covers_it(self):
        assert measured_top(scanned_counter(), counter_surface()) == pytest.approx(COUNTER_TOP, abs=0.01)

    def test_a_refitted_counter_still_stands_on_the_floor(self):
        refitted = measure_worktops(graph_of(scanned_counter()), counter_surface())
        assert len(refitted) == 1
        counter = refitted[0]
        assert counter.dimensions.z == pytest.approx(COUNTER_TOP, abs=0.01)
        assert counter.transform.position.z - counter.dimensions.z / 2 == pytest.approx(0.0, abs=1e-9)

    def test_a_register_on_the_counter_does_not_raise_its_top(self):
        register = slab((0.5, 0.0, COUNTER_TOP + 0.08), (0.3, 0.3, 0.16))
        points = np.vstack([counter_surface(), register])
        assert measured_top(scanned_counter(), points) == pytest.approx(COUNTER_TOP, abs=0.01)

    def test_a_top_the_mesh_barely_saw_keeps_the_box(self):
        corner = slab((0.7, 0.2, COUNTER_TOP), (0.2, 0.2, 0.0))
        assert measured_top(scanned_counter(), corner) is None

    def test_a_stool_is_not_read_as_a_worktop(self):
        stool = piece("Chair", (0.0, 0.0, 0.45), (0.45, 0.5, 0.9))
        seat = slab((0.0, 0.0, 0.76), (0.45, 0.5, 0.0))
        assert measure_worktops(graph_of(stool), seat) == []


def camera(frame_id, position, looking_at, width=640, height=480, focal=500.0) -> PhotoCamera:
    forward = np.asarray(looking_at, dtype=float) - np.asarray(position, dtype=float)
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    rotation = np.stack([right, down, forward])
    return PhotoCamera(
        frame_id=frame_id,
        room_to_camera=np.vstack([
            np.hstack([rotation, (-rotation @ np.asarray(position, dtype=float)).reshape(3, 1)]),
            [0.0, 0.0, 0.0, 1.0],
        ]),
        fx=focal, fy=focal, cx=width / 2, cy=height / 2, width=width, height=height, timestamp=0.0,
    )


class TestNamingTheCounter:
    """RoomPlan has no counter category; the photos settle what its storage box is."""

    BOX = (170.0, 165.0, 470.0, 325.0)
    """Inside the counter's own outline from three metres back, which projects to about (159, 160, 481, 331)."""

    def vote(self, node, names):
        frames = [camera(f"f{index}", (0.0, -3.0, 1.2), (0.0, 0.0, 0.45)) for index in range(len(names))]
        detections = {
            frame.frame_id: [Detection(frame_id=frame.frame_id, name=name, box=self.BOX, movable=False,
                                       confidence=0.95)]
            for frame, name in zip(frames, names)
        }
        return apply_secondary_semantic_corrections(graph_of(node), detections, frames).by_id(node.id)

    def test_storage_every_photo_calls_a_counter_is_a_counter(self):
        renamed = self.vote(scanned_counter("Storage"), ["counter", "counter", "counter"])
        assert renamed.label == "Counter"
        assert renamed.dimensions == scanned_counter("Storage").dimensions

    def test_storage_most_photos_call_a_bin_stays_storage(self):
        names = ["recycling bin", "recycling bin", "recycling bin", "counter", "counter"]
        assert self.vote(scanned_counter("Storage"), names).label == "Storage"

    def test_a_bar_the_photos_call_a_counter_stays_a_table(self):
        """Customers sit at it; a table and a counter are one family, so the scanned name stands."""
        assert self.vote(scanned_counter("Table"), ["counter", "counter", "counter"]).label == "Table"


class TestFragmentsOfScannedPieces:
    def test_a_counter_lid_carved_above_the_scanned_box_is_that_counter(self):
        lid = carved("counter", slab((0.0, 0.0, 0.84), (1.6, 0.6, 0.12)))
        assert part_of_a_scanned_piece(lid, graph_of(scanned_counter()))

    def test_a_backrest_leaning_past_the_stool_box_is_that_stool(self):
        stool = piece("Chair", (0.0, 0.0, 0.45), (0.45, 0.5, 0.9))
        backrest = carved("chair", slab((0.0, 0.29, 0.8), (0.4, 0.06, 0.16)))
        assert part_of_a_scanned_piece(backrest, graph_of(stool))

    def test_a_card_reader_on_the_counter_is_its_own_object(self):
        reader = carved("card reader", slab((0.4, 0.0, 0.93), (0.1, 0.15, 0.12)))
        assert not part_of_a_scanned_piece(reader, graph_of(scanned_counter()))

    def test_a_cup_on_a_table_edge_is_not_the_table(self):
        table = piece("Table", (0.0, 0.0, 0.37), (1.2, 0.8, 0.74))
        cup = carved("cup", slab((0.55, 0.0, 0.8), (0.08, 0.08, 0.12)))
        assert not part_of_a_scanned_piece(cup, graph_of(table))


class TestCountersStandOnTheFloor:
    def test_a_carved_counter_top_with_a_front_below_it_reaches_the_floor(self):
        top_slab = slab((3.0, 0.0, 0.88), (1.5, 0.5, 0.08))
        front = slab((3.0, -0.24, 0.42), (1.5, 0.0, 0.8))
        counter = carved("counter", top_slab)
        stood = standing_on_the_floor(counter, graph_of(), np.vstack([top_slab, front]))
        assert stood is not None
        assert stood.box.floor_clearance == pytest.approx(0.0, abs=1e-9)
        assert top(stood.box) == pytest.approx(top(counter.box))

    def test_a_counter_named_shelf_held_up_by_nothing_is_dropped(self):
        shelf = slab((3.0, 0.0, 1.4), (0.8, 0.3, 0.1))
        assert standing_on_the_floor(carved("counter", shelf), graph_of(), shelf) is None

    def test_chair_legs_in_one_corner_under_a_table_end_are_not_its_body(self):
        top_slab = slab((3.0, 0.0, 0.7), (0.6, 0.3, 0.06))
        legs = np.vstack([slab((2.75, -0.1, 0.33), (0.02, 0.02, 0.6)), slab((2.8, -0.08, 0.33), (0.02, 0.02, 0.6))])
        assert standing_on_the_floor(carved("table", top_slab), graph_of(), np.vstack([top_slab, legs])) is None

    def test_a_carved_lid_resting_on_a_scanned_counter_is_dropped(self):
        lid = slab((0.0, 0.0, 0.93), (1.0, 0.4, 0.1))
        assert standing_on_the_floor(carved("counter", lid), graph_of(scanned_counter()), lid) is None

    def test_things_that_are_not_tables_or_counters_are_left_alone(self):
        sign = carved("sign", slab((3.0, 0.0, 1.4), (0.4, 0.1, 0.3)))
        assert standing_on_the_floor(sign, graph_of(), sign.box.points) is sign


def test_a_box_stood_on_a_height_keeps_its_top():
    box = fit_box(slab((0.0, 0.0, 1.0), (0.2, 0.2, 0.2)))
    assert box is not None
    lowered = box.standing_on(0.5)
    assert lowered.floor_clearance == pytest.approx(0.5)
    assert top(lowered) == pytest.approx(top(box))
    assert replace(lowered, points=box.points).yaw == box.yaw
