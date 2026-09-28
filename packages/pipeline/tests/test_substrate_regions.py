"""The region operators, against rooms that were really scanned.

No region is built here. Every assertion is a property that has to hold of real
geometry, because a box written to make an operator fire contains exactly the
structure whoever wrote it thought to put there.
"""

from __future__ import annotations

import dataclasses
import json
import math
import pathlib
from itertools import combinations

import numpy as np
import pytest
from standardphysics_pipeline.ingest import parse_room_json
from standardphysics_pipeline.substrate import (
    Region,
    adjacency,
    bounds,
    corners,
    extent_along,
    footprint_overlap,
    frames_seeing,
    free_space,
    gap,
    gravity,
    image_of,
    overlap_fraction,
    principal_axes,
    region_of,
    regions,
    relative_offset,
    separation,
    volume,
)
from standardphysics_pipeline.textures.camera import CameraMetadataError, load_cameras

ROOT = pathlib.Path(__file__).resolve().parents[3]
PHONE = ROOT / "datasets/phone"

RESTING_GAP = 0.05
"""The tolerance the experiment in docs/ARCHITECTURE.md used for "sitting on"."""


def _scans():
    return [(path.parent, parse_room_json(json.loads(path.read_text()))) for path in sorted(PHONE.glob("*/room.json"))]


@pytest.fixture(scope="module")
def rooms():
    found = [(directory, graph) for directory, graph in _scans() if graph.nodes]
    if not found:
        pytest.skip("no scanned room on this machine")
    return found


def _up(graph) -> np.ndarray:
    return -gravity(graph)


def _lowest(found: list[Region], up: np.ndarray) -> Region:
    """The region whose top is lowest: whatever the rest stand on, found without a name."""
    return min(found, key=lambda region: extent_along(region, up)[1])


class TestARegionCarriesNoName:
    def test_the_only_fields_are_measurements(self):
        assert {field.name for field in dataclasses.fields(Region)} == {"id", "pose", "extents", "markings"}


class TestGravityIsRead:
    def test_it_is_a_unit_vector_pointing_down_the_room(self, rooms):
        for directory, graph in rooms:
            down = gravity(graph)
            assert math.isclose(float(np.linalg.norm(down)), 1.0, abs_tol=1e-9), directory.name
            assert down @ np.array([0.0, 0.0, -1.0]) > 0.999, directory.name


class TestOneRegion:
    def test_a_region_lies_wholly_within_itself(self, rooms):
        for _directory, graph in rooms:
            for region in regions(graph):
                assert overlap_fraction(region, region) == 1.0

    def test_its_volume_is_its_extents_multiplied(self, rooms):
        for _directory, graph in rooms:
            for node in graph.nodes:
                x, y, z = node.dimensions.as_tuple()
                assert math.isclose(volume(region_of(node)), x * y * z, rel_tol=1e-9, abs_tol=1e-12)

    def test_its_bounds_hold_every_corner(self, rooms):
        for _directory, graph in rooms:
            for region in regions(graph):
                low, high = bounds(region)
                points = corners(region)
                assert np.all(points >= low - 1e-9) and np.all(points <= high + 1e-9)

    def test_its_axes_are_square_and_longest_first(self, rooms):
        for _directory, graph in rooms:
            for region in regions(graph):
                axes = principal_axes(region)
                directions = np.array([direction for direction, _ in axes])
                assert np.allclose(directions @ directions.T, np.eye(3), atol=1e-6)
                lengths = [length for _, length in axes]
                assert lengths == sorted(lengths, reverse=True)

    def test_seen_from_itself_it_sits_at_the_origin(self, rooms):
        for _directory, graph in rooms:
            for region in regions(graph):
                assert np.allclose(relative_offset(region, region), np.eye(4), atol=1e-9)


class TestTwoRegions:
    def test_an_offset_carries_one_pose_onto_the_other(self, rooms):
        for _directory, graph in rooms:
            for a, b in combinations(regions(graph), 2):
                assert np.allclose(b.pose @ relative_offset(a, b), a.pose, atol=1e-9)

    def test_a_gap_reads_the_same_from_either_side(self, rooms):
        for directory, graph in rooms:
            up = _up(graph)
            for a, b in combinations(regions(graph), 2):
                assert math.isclose(gap(a, b, up), gap(b, a, -up), abs_tol=1e-9), directory.name

    def test_fractions_stay_between_nothing_and_everything(self, rooms):
        for _directory, graph in rooms:
            up = _up(graph)
            for a, b in combinations(regions(graph), 2):
                assert 0.0 <= overlap_fraction(a, b) <= 1.0
                assert 0.0 <= footprint_overlap(a, b, up) <= 1.0 + 1e-9

    def test_a_clear_gap_upward_is_never_more_than_the_separation(self, rooms):
        """Straight up is one of the directions that can part two upright boxes."""
        for directory, graph in rooms:
            up = _up(graph)
            for a, b in combinations(regions(graph), 2):
                clear = max(gap(a, b, up), gap(b, a, up))
                assert separation(a, b)[0] >= clear - 1e-9, directory.name

    def test_regions_that_share_volume_are_not_separated(self, rooms):
        """Within the micron `contains` allows, since a face lying on a face counts as inside."""
        for directory, graph in rooms:
            for a, b in combinations(regions(graph), 2):
                if overlap_fraction(a, b) > 0:
                    assert separation(a, b)[0] <= 1e-6, directory.name


class TestSupportComposesOutOfTheOperators:
    """The predicate from the architecture experiment, built from these operators alone."""

    def test_something_in_every_room_rests_on_the_lowest_surface(self, rooms):
        for directory, graph in rooms:
            up = _up(graph)
            found = regions(graph)
            lowest = _lowest(found, up)
            resting = [
                region
                for region in found
                if region.id != lowest.id
                and abs(gap(region, lowest, up)) <= RESTING_GAP
                and footprint_overlap(region, lowest, up) > 0.5
            ]
            assert resting, directory.name
            assert all(adjacency(region, lowest, within=RESTING_GAP).meets for region in resting), directory.name

    def test_resting_regions_touch_over_real_area(self, rooms):
        for directory, graph in rooms:
            up = _up(graph)
            found = regions(graph)
            lowest = _lowest(found, up)
            touching = [adjacency(region, lowest, within=RESTING_GAP) for region in found if region.id != lowest.id]
            assert any(contact.meets and contact.area > 0.01 for contact in touching), directory.name


class TestFreeSpace:
    def test_nothing_in_the_way_is_an_unbounded_width(self, rooms):
        for _directory, graph in rooms:
            found = regions(graph)
            assert free_space(found[0], found[1], [], _up(graph), height=2.0) == math.inf

    def test_every_width_between_two_things_is_real_and_not_negative(self, rooms):
        for directory, graph in rooms:
            up = _up(graph)
            found = regions(graph)
            lowest = _lowest(found, up)
            standing = [r for r in found if abs(gap(r, lowest, up)) <= RESTING_GAP and r.id != lowest.id]
            for start, end in combinations(standing[:6], 2):
                width = free_space(start, end, found, up, height=2.0)
                assert width >= 0 and not math.isnan(width), directory.name


class TestFrames:
    def test_a_frame_that_saw_a_region_holds_it_in_front_of_the_lens(self, rooms):
        checked = 0
        for directory, graph in rooms:
            cameras = _cameras(directory, graph)
            for region in regions(graph):
                for camera in cameras:
                    view = image_of(region, camera)
                    if view is None:
                        continue
                    left, top, right, bottom = view.box
                    assert view.depth > 0
                    assert 0 <= left <= right <= camera.width and 0 <= top <= bottom <= camera.height
                    checked += 1
        if not checked:
            pytest.skip("no scan here has photographs with camera metadata")

    def test_most_of_a_photographed_room_was_seen(self, rooms):
        for directory, graph in rooms:
            cameras = _cameras(directory, graph)
            if not cameras:
                continue
            seen = [region for region in regions(graph) if frames_seeing(region, cameras)]
            assert len(seen) >= len(graph.nodes) / 2, directory.name


def _cameras(directory: pathlib.Path, graph):
    manifest = directory / "photo-manifest.json"
    if not manifest.is_file() or graph.capture_to_room is None:
        return []
    frame_ids = [frame["frame_id"] for frame in json.loads(manifest.read_text())["frames"]]
    try:
        return load_cameras(directory / "poses.json", frame_ids, graph.capture_to_room)
    except CameraMetadataError:
        return []
