"""Surfaces found in the mesh, against the rooms that were really scanned.

No room is built here. Every assertion is a property that has to hold of a real
scan, because a mesh written to make a rule fire contains exactly the structure
whoever wrote it thought to put there, which is the assumption this layer exists
to remove.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest
from standardphysics_pipeline.ingest import parse_room_json
from standardphysics_pipeline.substrate import planes_of

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCANS = ROOT / "services/api/var/scans"


def _scanned() -> list[pathlib.Path]:
    if not SCANS.is_dir():
        return []
    return [
        candidate
        for candidate in sorted(SCANS.glob("*"))
        if (candidate / "artifacts" / "lidar-mesh").is_file()
        and (candidate / "artifacts" / "room-json").is_file()
    ]


@pytest.fixture(scope="module")
def rooms() -> list[tuple[str, object, list]]:
    found = _scanned()
    if not found:
        pytest.skip("no scan with a mesh on this machine")
    out = []
    for directory in found:
        art = directory / "artifacts"
        graph = parse_room_json(json.loads((art / "room-json").read_text()))
        out.append((directory.name, graph, planes_of(art / "lidar-mesh", graph.capture_to_room)))
    return out


class TestSurfacesComeOutOfTheMesh:
    def test_every_scanned_room_yields_surfaces(self, rooms):
        for name, _graph, planes in rooms:
            assert planes, f"{name} produced nothing"

    def test_a_surface_carries_real_extent(self, rooms):
        for _name, _graph, planes in rooms:
            for plane in planes:
                assert plane.area > 0
                assert plane.length >= plane.breadth >= 0
                assert plane.highest >= plane.lowest

    def test_a_facing_is_a_direction(self, rooms):
        for _name, _graph, planes in rooms:
            for plane in planes:
                assert float(np.linalg.norm(plane.normal)) == pytest.approx(1.0, abs=1e-6)

    def test_every_room_has_flat_surface_low_down_and_high_up(self, rooms):
        """A room has a floor and something over it, whatever else it has.

        Not that the two largest flat runs are those two. One of these scans is a
        space with a 4.7 m ceiling, and its floor comes back as forty-seven pieces
        because the furniture standing on it interrupts every one of them, so the
        ceiling carries more unbroken area than any single piece of floor. The
        floor is still there, and that is what this asserts.
        """
        for name, _graph, planes in rooms:
            flat = [plane for plane in planes if plane.upright < 0.2]
            assert flat, f"{name} found no horizontal surface"
            low = sum(plane.area for plane in flat if plane.centre[2] < 0.5)
            high = sum(plane.area for plane in flat if plane.centre[2] > 1.8)
            assert low > 1.0, f"{name} found {low:.2f} m2 of floor"
            assert high > 1.0, f"{name} found {high:.2f} m2 of anything overhead"


class TestItFindsWhatTheScannerDidNot:
    """The point of the layer: a room is richer than somebody else's category list."""

    def _unboxed(self, graph, planes) -> list:
        """Upright runs standing where the room model has no node of that height."""
        boxed = [
            (np.array(node.transform.position.as_tuple()), node.dimensions.z)
            for node in graph.nodes
        ]
        loose = []
        for plane in planes:
            if plane.upright < 0.8 or plane.length < 1.0:
                continue
            near = [
                height
                for at, height in boxed
                if float(np.linalg.norm((at - plane.centre)[:2])) < 0.6
            ]
            if not any(abs(height - plane.highest) < 0.4 for height in near):
                loose.append(plane)
        return loose

    def test_some_upright_surface_is_absent_from_the_room_model(self, rooms):
        for name, graph, planes in rooms:
            assert self._unboxed(graph, planes), (
                f"{name} found nothing the scanner missed, which would mean this "
                "layer is telling us only what we already had"
            )

    def test_a_partition_standing_below_head_height_is_recovered(self, rooms):
        """A low screen over a desk is the shape a category list has no room for.

        Described by its measurements rather than by what it is called, so that
        recovering it cannot be arranged by recognising a word.
        """
        found = [
            (name, plane)
            for name, graph, planes in rooms
            for plane in self._unboxed(graph, planes)
            if plane.length >= 2.0 and 0.9 <= plane.highest <= 1.6
        ]
        assert found, (
            "no scan yielded an upright run over 2 m long topping out below head "
            "height; the Gardner Stacks mesh holds one"
        )
