"""Small objects measured on the mesh, pinned against geometry with a known answer.

On a real café capture the same fire extinguisher came out 44.4 inches tall at
the top in one detection run and 50.3 in another, while the mesh showed it
standing off the wall up to about 53. Its height has to come from the mesh, so
that which rectangles a detector drew this time cannot move an ADA finding.
"""

from __future__ import annotations

import uuid

import numpy as np
import pytest
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_pipeline.coords import capture_to_room
from standardphysics_pipeline.discovery.boxes import claimed_by_any
from standardphysics_pipeline.discovery.carve import FrameView, carve, fit_box
from standardphysics_pipeline.discovery.detect import Detection
from standardphysics_pipeline.discovery.extent import MeshViews, grown_to_the_mesh, measured_on_the_mesh
from standardphysics_pipeline.discovery.merge import Candidate, merge_candidates
from standardphysics_pipeline.discovery.people import without_people
from standardphysics_pipeline.textures.camera import PhotoCamera

EXTINGUISHER = ((0.0, 1.85, 1.1), (0.14, 0.08, 0.6))
"""Standing off a wall at y = 2, from 0.8 m to 1.4 m."""


def slab(centre, size, spacing=0.02) -> np.ndarray:
    ranges = [np.arange(c - s / 2, c + s / 2 + spacing / 2, spacing) for c, s in zip(centre, size, strict=False)]
    grid = np.meshgrid(*ranges, indexing="ij")
    return np.stack([axis.ravel() for axis in grid], axis=1)


def top(box) -> float:
    return box.centre[2] + box.dimensions[2] / 2


def bottom(box) -> float:
    return box.centre[2] - box.dimensions[2] / 2


def camera_at(frame_id, position, looking_at, width=640, height=480, focal=500.0) -> PhotoCamera:
    forward = np.asarray(looking_at, dtype=float) - np.asarray(position, dtype=float)
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    rotation = np.stack([right, np.cross(forward, right), forward])
    return PhotoCamera(
        frame_id=frame_id,
        room_to_camera=np.vstack([
            np.hstack([rotation, (-rotation @ np.asarray(position, dtype=float)).reshape(3, 1)]),
            [0.0, 0.0, 0.0, 1.0],
        ]),
        fx=focal, fy=focal, cx=width / 2 - 0.5, cy=height / 2 - 0.5,
        width=width, height=height, timestamp=0.0,
    )


def rectangle_round(camera: PhotoCamera, points: np.ndarray, pad: float = 3.0) -> tuple:
    columns, rows, _ = camera.project(points)
    return (columns.min() - pad, rows.min() - pad, columns.max() + pad, rows.max() + pad)


def wall_at(y: float) -> SceneNode:
    return SceneNode(
        id=uuid.uuid4(), kind="wall", label="Wall", raw_category="wall",
        dimensions=Vec3(x=4.0, y=0.0, z=2.4),
        transform=Mat4(m=[1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, y, 0.0, 0.0, 1.0, 1.2, 0.0, 0.0, 0.0, 1.0]),
    )


class TestReadingTheHeightOffTheMesh:
    def test_a_carve_cut_off_by_the_photo_edge_grows_to_the_whole_object(self):
        body = slab(*EXTINGUISHER)
        carved = fit_box(body[body[:, 2] < 1.1])
        grown = grown_to_the_mesh(carved, body)
        assert top(carved) < 1.12
        assert top(grown) == pytest.approx(1.4, abs=0.02)
        assert bottom(grown) == pytest.approx(0.8, abs=0.02)
        assert grown.dimensions[:2] == carved.dimensions[:2]

    def test_a_shelf_just_over_it_is_not_swallowed(self):
        """The shelf reaches past the footprint, so it is something broader than the object."""
        body = slab(*EXTINGUISHER)
        shelf = slab((0.0, 1.85, 1.43), (0.8, 0.3, 0.04))
        carved = fit_box(body[body[:, 2] < 1.1])
        assert top(grown_to_the_mesh(carved, np.vstack([body, shelf]))) == pytest.approx(1.4, abs=0.02)

    def test_a_sign_above_a_hand_width_of_air_is_not_reached(self):
        body = slab(*EXTINGUISHER)
        sign = slab((0.0, 1.85, 1.6), (0.14, 0.08, 0.2))
        carved = fit_box(body[body[:, 2] < 1.1])
        assert top(grown_to_the_mesh(carved, np.vstack([body, sign]))) == pytest.approx(1.4, abs=0.02)

    def test_a_neighbour_carved_as_its_own_object_is_left_to_it(self):
        body = slab(*EXTINGUISHER)
        dispenser = slab((0.0, 1.85, 1.55), (0.14, 0.08, 0.3))
        carved = fit_box(body[body[:, 2] < 1.1])
        grown = grown_to_the_mesh(carved, np.vstack([body, dispenser]), [fit_box(dispenser)])
        assert top(grown) == pytest.approx(1.4, abs=0.02)

    def test_a_body_still_going_at_the_cap_is_part_of_something_larger(self):
        """A screen on a pole: the pole runs a metre down, and the screen's underside stays where it was carved."""
        screen = slab((0.0, 0.0, 1.5), (0.3, 0.1, 0.2))
        pole = slab((0.0, 0.0, 0.7), (0.06, 0.06, 1.4))
        carved = fit_box(screen)
        grown = grown_to_the_mesh(carved, np.vstack([screen, pole]))
        assert bottom(grown) == pytest.approx(bottom(carved), abs=1e-9)

    def test_an_object_with_nothing_more_in_the_mesh_keeps_its_carve(self):
        body = slab(*EXTINGUISHER)
        carved = fit_box(body)
        assert grown_to_the_mesh(carved, np.empty((0, 3))) == carved
        assert grown_to_the_mesh(carved, body).dimensions == pytest.approx(carved.dimensions, abs=0.01)

    def test_a_broad_piece_is_left_as_carved(self):
        counter_lid = slab((0.0, 0.0, 0.9), (1.2, 0.5, 0.08))
        body = slab((0.0, 0.0, 0.5), (1.2, 0.5, 0.7))
        carved = fit_box(counter_lid)
        assert grown_to_the_mesh(carved, np.vstack([counter_lid, body])) == carved

    def test_the_order_objects_are_measured_in_changes_nothing(self):
        body = slab(*EXTINGUISHER)
        dispenser = slab((0.0, 1.85, 1.6), (0.14, 0.08, 0.3))
        found = merge_candidates([
            Candidate(Detection("frame-0001", "fire extinguisher", (0, 0, 1, 1), False, 0.9),
                      fit_box(body[body[:, 2] < 1.1])),
            Candidate(Detection("frame-0001", "soap dispenser", (0, 0, 1, 1), False, 0.9), fit_box(dispenser)),
        ])
        mesh = np.vstack([body, dispenser])
        forward = {one.name: one.box for one in measured_on_the_mesh(found, mesh)}
        backward = {one.name: one.box for one in measured_on_the_mesh(found[::-1], mesh)}
        assert top(forward["fire extinguisher"]) == pytest.approx(1.4, abs=0.02)
        for name, box in forward.items():
            assert box.centre == backward[name].centre and box.dimensions == backward[name].dimensions


class TestTheSameObjectFromDifferentDetections:
    """Two detection runs over the same scan box the extinguisher differently and must agree on its height.

    The first run boxes its lower half, the way a photo that cuts it off at the
    edge does. The second boxes most of it from another place, and also draws a
    person over its top in one photo, which is what took half its points out
    of one real run: carving loses that top, and measuring must not.
    """

    def _measured_top(self, points, graph, views) -> float:
        removal = without_people(points, graph, views)
        unclaimed = removal.points[~claimed_by_any(removal.points, graph)]
        candidates = [
            Candidate(detection, box)
            for camera, detections, _ in views
            for detection in detections
            if not detection.is_person
            and (box := carve(FrameView.of(unclaimed, camera), detection)) is not None
        ]
        objects = merge_candidates(candidates)
        assert len(objects) == 1
        measured = measured_on_the_mesh(objects, MeshViews(points, views).loose_near(objects, graph, removal.volumes))
        return top(measured[0].box)

    def test_both_runs_read_the_same_height(self):
        body = slab(*EXTINGUISHER)
        wall = slab((0.0, 2.0, 1.2), (3.0, 0.02, 2.4))
        points = np.vstack([body, wall])
        graph = SceneGraph(scan_id=uuid.uuid4(), nodes=[wall_at(2.0)], capture_to_room=capture_to_room(0.0))
        ahead = camera_at("frame-0001", (0.0, 0.2, 1.1), (0.0, 1.85, 1.1))
        aside = camera_at("frame-0002", (0.7, 0.3, 1.3), (0.0, 1.85, 1.1))
        lower_half = body[body[:, 2] < 1.1]
        upper_part = body[body[:, 2] > 1.2]

        def seen(camera, name, part):
            return Detection(camera.frame_id, name, rectangle_round(camera, part), name != "person", 0.9)

        first = [
            (ahead, [seen(ahead, "fire extinguisher", lower_half)], None),
            (aside, [seen(aside, "fire extinguisher", lower_half)], None),
        ]
        second = [
            (ahead, [seen(ahead, "extinguisher", body[body[:, 2] < 1.3])], None),
            (aside, [seen(aside, "extinguisher", body[body[:, 2] < 1.3]), seen(aside, "person", upper_part)], None),
        ]
        first_top, second_top = self._measured_top(points, graph, first), self._measured_top(points, graph, second)
        assert first_top == pytest.approx(second_top, abs=0.01)
        assert first_top == pytest.approx(1.4, abs=0.03)
