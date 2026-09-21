"""What discovery must get right, pinned against geometry with a known answer.

The vision model's rectangles are the one thing these cannot check, because a
detector is not a function with an expected output. Everything the rectangles
feed is checkable: which points a camera can see through them, which piece of
the room those points belong to, what box fits it, and what happens when two
frames see the same thing.

The projection underneath all of this is pinned separately in test_camera.py.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import replace

import numpy as np
import pytest
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_pipeline.coords import capture_to_room
from standardphysics_pipeline.discovery.boxes import (
    claimed_by,
    claimed_by_any,
    resting_parent,
    structure_points,
)
from standardphysics_pipeline.discovery.carve import FrameView, carve, fit_box
from standardphysics_pipeline.discovery.clusters import voxel_components, without_the_surface_beneath
from standardphysics_pipeline.discovery.detect import Detection, EncodedFrame, _pixel_box
from standardphysics_pipeline.discovery.discover import _viewpoints, _worth_keeping
from standardphysics_pipeline.discovery.merge import Candidate, DiscoveredObject, merge_candidates
from standardphysics_pipeline.discovery.people import without_people
from standardphysics_pipeline.textures.camera import PhotoCamera


def slab(centre, size, spacing=0.02) -> np.ndarray:
    """A filled box of points, the way LiDAR would leave a solid object."""
    ranges = [
        np.arange(c - s / 2, c + s / 2 + spacing / 2, spacing)
        for c, s in zip(centre, size)
    ]
    grid = np.meshgrid(*ranges, indexing="ij")
    return np.stack([axis.ravel() for axis in grid], axis=1)


def camera_at(position, looking_at, width=640, height=480, focal=500.0) -> PhotoCamera:
    """A camera in the room frame, +X right, +Y down, +Z forward."""
    forward = np.asarray(looking_at, dtype=float) - np.asarray(position, dtype=float)
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    rotation = np.stack([right, down, forward])
    return PhotoCamera(
        frame_id="frame-0001",
        room_to_camera=np.vstack([
            np.hstack([rotation, (-rotation @ np.asarray(position, dtype=float)).reshape(3, 1)]),
            [0.0, 0.0, 0.0, 1.0],
        ]),
        fx=focal, fy=focal, cx=width / 2 - 0.5, cy=height / 2 - 0.5,
        width=width, height=height, timestamp=0.0,
    )


def box_around(camera: PhotoCamera, points: np.ndarray, pad: float = 4.0) -> tuple:
    columns, rows, _ = camera.project(points)
    return (columns.min() - pad, rows.min() - pad, columns.max() + pad, rows.max() + pad)


def node(label, centre, size, *, kind="object", yaw=0.0, movable=True) -> SceneNode:
    cos_t, sin_t = math.cos(yaw), math.sin(yaw)
    return SceneNode(
        id=uuid.uuid4(), kind=kind, label=label, raw_category=label.lower(),
        dimensions=Vec3(x=size[0], y=size[1], z=size[2]),
        transform=Mat4(m=[
            cos_t, -sin_t, 0.0, centre[0],
            sin_t, cos_t, 0.0, centre[1],
            0.0, 0.0, 1.0, centre[2],
            0.0, 0.0, 0.0, 1.0,
        ]),
        movable=movable,
    )


def graph_of(*nodes) -> SceneGraph:
    return SceneGraph(scan_id=uuid.uuid4(), nodes=list(nodes), capture_to_room=capture_to_room(0.0))


class TestFittingABox:
    def test_a_slab_comes_back_at_the_size_it_was_built(self):
        box = fit_box(slab((1.0, 2.0, 0.5), (0.4, 0.3, 0.2)))
        assert box.dimensions == pytest.approx((0.4, 0.3, 0.2), abs=0.02)
        assert box.centre == pytest.approx((1.0, 2.0, 0.5), abs=0.02)

    def test_a_turned_slab_keeps_its_extents_and_reports_the_turn(self):
        points = slab((0.0, 0.0, 0.5), (0.8, 0.2, 0.2))
        yaw = math.radians(30)
        turned = points @ np.asarray([
            [math.cos(yaw), -math.sin(yaw), 0.0],
            [math.sin(yaw), math.cos(yaw), 0.0],
            [0.0, 0.0, 1.0],
        ]).T
        box = fit_box(turned)
        assert sorted(box.dimensions[:2]) == pytest.approx([0.2, 0.8], abs=0.03)
        assert math.degrees(box.yaw) == pytest.approx(30, abs=3)

    def test_a_smear_across_one_surface_is_not_an_object(self):
        """A flat sheet has no depth, and a box fitted to it would invent one."""
        assert fit_box(slab((0.0, 0.0, 0.8), (0.5, 0.5, 0.004))) is None

    def test_a_box_bigger_than_any_furniture_is_refused(self):
        assert fit_box(slab((0.0, 0.0, 1.0), (6.0, 0.5, 0.5))) is None


class TestSeparatingWhatIsInFrontOfWhat:
    def test_points_that_touch_are_one_piece_and_a_gap_makes_two(self):
        near, far = slab((0.0, 0.0, 1.0), (0.2, 0.2, 0.2)), slab((1.0, 0.0, 1.0), (0.2, 0.2, 0.2))
        labels = voxel_components(np.vstack([near, far]))
        assert len(np.unique(labels)) == 2
        assert len(np.unique(voxel_components(np.vstack([near, near + [0.03, 0, 0]])))) == 1

    def test_the_wall_behind_a_rectangle_is_not_carved_into_the_object(self):
        """The classic failure: a box drawn round a terminal also covers the wall."""
        terminal = slab((0.0, 1.0, 1.0), (0.2, 0.1, 0.15))
        wall = slab((0.0, 4.0, 1.0), (3.0, 0.05, 2.0))
        camera = camera_at((0.0, -1.0, 1.0), (0.0, 1.0, 1.0))
        both = np.vstack([terminal, wall])
        detection = Detection("frame-0001", "payment terminal", box_around(camera, terminal), True, 0.9)
        box = carve(FrameView.of(both, camera), detection)
        assert box.dimensions == pytest.approx((0.2, 0.1, 0.15), abs=0.04)
        assert box.centre[1] == pytest.approx(1.0, abs=0.05)

    def test_a_hidden_object_is_not_carved_through_the_thing_in_front_of_it(self):
        front, behind = slab((0.0, 1.0, 1.0), (0.6, 0.1, 0.6)), slab((0.0, 2.0, 1.0), (0.3, 0.1, 0.3))
        camera = camera_at((0.0, -1.0, 1.0), (0.0, 1.0, 1.0))
        both = np.vstack([front, behind])
        from standardphysics_pipeline.textures.project import depth_buffer

        view = FrameView.of(both, camera, depth_buffer(camera, both))
        seen = view.points[view.through(Detection("frame-0001", "screen", box_around(camera, behind), True, 0.9))]
        assert len(seen) and seen[:, 1].max() < 1.5


class TestWhatTheScanAlreadyKnows:
    def test_a_node_claims_the_points_inside_it_and_not_those_beside_it(self):
        desk = node("Desk", (0.0, 0.0, 0.37), (1.2, 0.6, 0.74))
        points = np.vstack([slab((0.0, 0.0, 0.4), (0.3, 0.3, 0.2)), slab((2.0, 0.0, 0.4), (0.3, 0.3, 0.2))])
        claimed = claimed_by_any(points, graph_of(desk))
        assert claimed[: len(points) // 2].all() and not claimed[len(points) // 2 :].any()

    def test_a_laptop_on_a_desk_is_not_claimed_by_the_desk(self):
        """Exactly why removing claimed points separates one from the other.

        Its underside rests on the desk, so the contact margin takes that layer.
        Everything above it survives, which is the laptop.
        """
        desk = node("Desk", (0.0, 0.0, 0.37), (1.2, 0.6, 0.74))
        laptop = slab((0.0, 0.0, 0.85), (0.3, 0.2, 0.2))
        survives = ~claimed_by_any(laptop, graph_of(desk))
        assert survives.mean() > 0.8
        assert laptop[survives][:, 2].min() > 0.74

    def test_a_laptop_is_parented_to_the_desk_it_stands_on(self):
        desk = node("Desk", (0.0, 0.0, 0.37), (1.2, 0.6, 0.74))
        box = fit_box(slab((0.0, 0.0, 0.85), (0.3, 0.2, 0.2)))
        assert resting_parent(box, graph_of(desk)) == desk.id

    def test_something_on_the_floor_has_no_parent(self):
        desk = node("Desk", (0.0, 0.0, 0.37), (1.2, 0.6, 0.74))
        box = fit_box(slab((2.0, 0.0, 0.1), (0.3, 0.2, 0.2)))
        assert resting_parent(box, graph_of(desk)) is None

    def test_walls_and_floors_are_structure_and_ordinary_furniture_is_not(self):
        wall = node("Wall", (0.0, 2.0, 1.2), (4.0, 0.1, 2.4), kind="wall")
        chair = node("Chair", (0.0, 0.0, 0.45), (0.5, 0.5, 0.9))
        on_wall = slab((0.0, 2.0, 1.2), (0.2, 0.05, 0.2))
        on_chair = slab((0.0, 0.0, 0.45), (0.2, 0.2, 0.2))
        structure = structure_points(np.vstack([on_wall, on_chair]), graph_of(wall, chair))
        assert structure[: len(on_wall)].all()
        assert not structure[len(on_wall) :].any()


class TestTakingPeopleOut:
    def test_the_surface_a_person_occupies_leaves_the_mesh(self):
        person = slab((0.0, 1.0, 1.0), (0.4, 0.3, 1.6))
        shelf = slab((2.0, 1.0, 1.0), (0.4, 0.3, 0.3))
        camera = camera_at((0.0, -1.5, 1.2), (0.0, 1.0, 1.0))
        points = np.vstack([person, shelf])
        detection = Detection("frame-0001", "person", box_around(camera, person), True, 0.95)
        removal = without_people(points, graph_of(), [(camera, [detection], None)])
        assert removal.removed >= len(person) * 0.8
        assert len(removal.points) >= len(shelf) * 0.9

    def test_a_wall_someone_stood_against_keeps_its_points(self):
        """A person cannot delete a wall, however thoroughly they covered it."""
        wall_node = node("Wall", (0.0, 2.0, 1.2), (4.0, 0.1, 2.4), kind="wall")
        wall = slab((0.0, 2.0, 1.2), (1.0, 0.05, 1.0))
        camera = camera_at((0.0, -1.5, 1.2), (0.0, 2.0, 1.2))
        everywhere = Detection("frame-0001", "person", (0.0, 0.0, camera.width, camera.height), True, 0.9)
        removal = without_people(wall, graph_of(wall_node), [(camera, [everywhere], None)])
        assert removal.removed == 0
        assert len(removal.points) == len(wall)

    def test_a_frame_with_nobody_in_it_removes_nothing(self):
        points = slab((0.0, 1.0, 1.0), (0.4, 0.3, 0.4))
        camera = camera_at((0.0, -1.5, 1.0), (0.0, 1.0, 1.0))
        chair = Detection("frame-0001", "chair", (0.0, 0.0, camera.width, camera.height), True, 0.9)
        removal = without_people(points, graph_of(), [(camera, [chair], None)])
        assert removal.removed == 0 and removal.frames_with_people == 0


class TestSeeingOneThingTwice:
    def _two_views(self, name_a: str, name_b: str, centre_b=(0.0, 1.0, 1.0)):
        box_a = fit_box(slab((0.0, 1.0, 1.0), (0.3, 0.2, 0.2)))
        box_b = fit_box(slab(centre_b, (0.3, 0.2, 0.2)))
        return [
            Candidate(Detection("frame-0001", name_a, (0, 0, 10, 10), True, 0.9), box_a),
            Candidate(Detection("frame-0002", name_b, (0, 0, 10, 10), True, 0.8), box_b),
        ]

    def test_the_same_thing_from_two_frames_is_one_object_with_two_views(self):
        merged = merge_candidates(self._two_views("laptop", "laptop"))
        assert len(merged) == 1 and merged[0].views == 2

    def test_two_words_for_one_thing_do_not_make_two_objects(self):
        assert len(merge_candidates(self._two_views("sofa", "couch"))) == 1

    def test_unrelated_names_in_one_place_still_collapse_to_one_object(self):
        """A bag from one side and a rucksack from the other occupy one space."""
        assert len(merge_candidates(self._two_views("bag", "holdall"))) == 1

    def test_two_laptops_on_one_desk_stay_two_laptops(self):
        assert len(merge_candidates(self._two_views("laptop", "laptop", centre_b=(1.2, 1.0, 1.0)))) == 2


class TestReadingTheModelsBoxes:
    def _frame(self):
        return EncodedFrame(jpeg=b"", width=1000, height=500)

    def test_a_box_arrives_as_top_left_bottom_right_in_stored_pixels(self):
        """Gemini writes [ymin, xmin, ymax, xmax] out of 1000, against each edge."""
        assert _pixel_box([100, 200, 300, 600], self._frame()) == pytest.approx((200.0, 50.0, 600.0, 150.0))

    def test_corners_given_the_wrong_way_round_are_still_read(self):
        assert _pixel_box([300, 600, 100, 200], self._frame()) == pytest.approx((200.0, 50.0, 600.0, 150.0))

    def test_a_box_off_the_edge_is_pulled_back_onto_the_picture(self):
        left, top, right, bottom = _pixel_box([-50, -50, 1200, 1200], self._frame())
        assert (left, top) == (0.0, 0.0) and (right, bottom) == (1000.0, 500.0)

    def test_a_box_too_thin_to_hold_any_points_is_refused(self):
        assert _pixel_box([500, 500, 500, 501], self._frame()) is None

    def test_anything_that_is_not_four_numbers_is_refused(self):
        assert _pixel_box([1, 2, 3], self._frame()) is None
        assert _pixel_box("500,500", self._frame()) is None


class TestNamingThings:
    def test_a_person_is_recognised_whatever_the_detector_called_them(self):
        for name in ("person", "Woman", "customer", "STAFF"):
            assert Detection("frame-0001", name, (0, 0, 1, 1), True, 0.9).is_person

    def test_furniture_is_not_a_person(self):
        assert not Detection("frame-0001", "chair", (0, 0, 1, 1), True, 0.9).is_person


class TestCountingSeparateLooks:
    """Keyframes land twice a second, so a run of them is one look, not twelve."""

    def _object(self, frame_ids):
        box = fit_box(slab((0.0, 1.0, 1.0), (0.3, 0.2, 0.2)))
        return DiscoveredObject(
            name="laptop", box=box, movable=True, confidence=0.9,
            frame_ids=tuple(frame_ids),
        )

    def _cameras(self, positions):
        return [
            replace(camera_at(position, (position[0], position[1] + 1.0, position[2])),
                    frame_id=f"frame-{index:04d}")
            for index, position in enumerate(positions)
        ]

    def test_standing_still_is_one_look_however_many_frames(self):
        cameras = self._cameras([(0.0, -1.0, 1.2), (0.05, -1.02, 1.2), (0.08, -0.99, 1.2)])
        assert _viewpoints(self._object([c.frame_id for c in cameras]), cameras) == 1

    def test_walking_around_something_is_several_looks(self):
        cameras = self._cameras([(0.0, -1.0, 1.2), (1.4, -1.0, 1.2), (2.8, -1.0, 1.2)])
        assert _viewpoints(self._object([c.frame_id for c in cameras]), cameras) == 3

    def test_frames_that_never_saw_it_do_not_count(self):
        cameras = self._cameras([(0.0, -1.0, 1.2), (1.4, -1.0, 1.2)])
        assert _viewpoints(self._object(["frame-0000"]), cameras) == 1


class TestShowingTheModelTheRoomUpright:
    """A phone held upright stores its photos on their side, and a detector
    shown a sideways room reads the laptop on someone's knees as a chair."""

    def _portrait(self):
        return EncodedFrame(jpeg=b"", width=1920, height=1440, turns=1)

    def test_a_box_the_model_drew_comes_back_in_sensor_pixels(self):
        """Turning the picture clockwise puts the sensor's top-left at the top-right."""
        # The model's whole frame must map to the whole sensor frame.
        assert _pixel_box([0, 0, 1000, 1000], self._portrait()) == pytest.approx((0.0, 0.0, 1920.0, 1440.0))

    def test_the_top_left_of_the_upright_picture_is_the_bottom_left_of_the_sensor(self):
        left, top, right, bottom = _pixel_box([0, 0, 100, 100], self._portrait())
        assert (left, right) == pytest.approx((0.0, 192.0))
        assert (top, bottom) == pytest.approx((1296.0, 1440.0))

    def test_a_frame_needing_no_turn_is_left_exactly_as_it_is(self):
        flat = EncodedFrame(jpeg=b"", width=1000, height=500, turns=0)
        assert _pixel_box([100, 200, 300, 600], flat) == pytest.approx((200.0, 50.0, 600.0, 150.0))

    def test_four_turns_is_the_same_as_none(self):
        once = EncodedFrame(jpeg=b"", width=1000, height=500, turns=1)
        spun = EncodedFrame(jpeg=b"", width=1000, height=500, turns=5)
        assert _pixel_box([100, 200, 300, 600], spun) == pytest.approx(_pixel_box([100, 200, 300, 600], once))

    def test_every_orientation_the_phone_reports_is_known(self):
        from standardphysics_pipeline.discovery.detect import QUARTER_TURNS_CLOCKWISE

        assert set(QUARTER_TURNS_CLOCKWISE) == {
            "portrait", "portrait_upside_down", "landscape_left", "landscape_right",
        }


class TestNotReDiscoveringTheRoom:
    def _object(self, name):
        return DiscoveredObject(
            name=name, box=fit_box(slab((0.0, 1.0, 1.0), (0.4, 0.3, 0.4))),
            movable=True, confidence=0.9, frame_ids=("frame-0001", "frame-0002"),
        )

    def test_the_shell_of_the_room_is_not_an_object_in_it(self):
        for name in ("wall", "floor", "ceiling", "door", "window", "blinds", "curtains"):
            assert not _worth_keeping(self._object(name), graph_of(), viewpoints=9), name

    def test_furniture_and_clutter_still_count(self):
        for name in ("laptop", "payment terminal", "kettlebell", "backpack", "desk"):
            assert _worth_keeping(self._object(name), graph_of(), viewpoints=9), name


class TestLeavingBehindWhatAThingRestsOn:
    def test_a_laptop_on_a_desktop_is_separated_from_it(self):
        """The desktop goes entirely. The laptop keeps everything but the
        layer of itself that is touching, which is the price of the cut."""
        desktop = slab((0.0, 0.0, 0.74), (1.2, 0.6, 0.02))
        laptop = slab((0.0, 0.0, 0.80), (0.3, 0.2, 0.10))
        points = np.vstack([desktop, laptop])
        kept = points[without_the_surface_beneath(points)]
        assert kept[:, 2].min() > 0.75
        assert 0.6 < len(kept) / len(laptop) <= 1.0
        assert len(kept) < len(desktop)

    def test_an_object_standing_alone_keeps_all_its_points(self):
        chair = slab((0.0, 0.0, 0.45), (0.5, 0.5, 0.9))
        assert without_the_surface_beneath(chair).all()

    def test_too_few_points_to_judge_are_left_alone(self):
        assert without_the_surface_beneath(slab((0.0, 0.0, 1.0), (0.05, 0.05, 0.05))).all()


class TestWhatAPieceOfFurnitureAccountsFor:
    def _desk(self):
        return node("Desk", (0.0, 0.0, 0.37), (1.2, 0.6, 0.74))

    def test_a_desk_accounts_for_its_own_body(self):
        assert claimed_by(slab((0.0, 0.0, 0.30), (0.3, 0.3, 0.2)), self._desk()).all()

    def test_a_desk_does_not_account_for_what_is_left_on_it(self):
        """Its box reaches 0.74; a generated box is least accurate right there."""
        on_top = slab((0.0, 0.0, 0.70), (0.3, 0.2, 0.04))
        assert not claimed_by(on_top, self._desk()).any()

    def test_a_wall_still_accounts_for_all_of_itself(self):
        wall = node("Wall", (0.0, 2.0, 1.2), (4.0, 0.1, 2.4), kind="wall")
        assert claimed_by(slab((0.0, 2.0, 2.3), (0.3, 0.05, 0.1)), wall).all()
