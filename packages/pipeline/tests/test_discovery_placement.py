"""Where scanned pieces stand, pinned against geometry with a known answer."""

from __future__ import annotations

import math
import uuid

import numpy as np
import pytest
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_pipeline.coords import capture_to_room
from standardphysics_pipeline.discovery.worktops import measure_worktops, measured_top


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
