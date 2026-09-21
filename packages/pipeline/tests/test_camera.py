"""The photo-to-model projection, pinned.

Every photo-driven feature stands on this module, and a sign error in it is
invisible: a flipped axis still lands every point somewhere plausible in the
image, just on the wrong thing. These tests fix the conventions so that a later
change has to argue with them.

The arrangement throughout: the camera sits at the ARKit origin with an identity
transform, so it looks down ARKit -Z with ARKit +Y towards the top of the
sensor. The room frame turns that Y-up world into Z-up, so a point two metres
in front of the camera is at room (0, 2, 0).

Every pose here is `image_orientation="sensor"`, because that is the only thing
`PoseRecord.projectable` accepts. The JPEG is written straight from the pixel
buffer and never turned, so the intrinsics describe it as stored; rotating for a
human to look at is a display concern and must not reach this maths.
"""

import numpy as np
import pytest
from standardphysics_contracts import PoseRecord
from standardphysics_pipeline.coords import capture_to_room
from standardphysics_pipeline.textures.camera import CameraMetadataError, camera_from_pose

FX = FY = 1000.0
CX, CY = 960.0, 720.0
CALIBRATION = (1920, 1440)


def pose(transform=None, image_size=CALIBRATION, calibration=CALIBRATION) -> PoseRecord:
    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    return PoseRecord(
        metadata_version=2,
        frame_id="frame-0000",
        image="frames/frame_0000.jpg",
        timestamp=0.0,
        transform=transform or identity,
        intrinsics=[FX, 0, 0, 0, FY, 0, CX, CY, 1],
        image_width=image_size[0],
        image_height=image_size[1],
        calibration_width=calibration[0],
        calibration_height=calibration[1],
        image_orientation="sensor",
    )


def camera(floor_height: float = 0.0):
    return camera_from_pose(pose(), capture_to_room(floor_height))


def test_straight_ahead_lands_on_the_principal_point():
    u, v, depth = camera().project(np.array([[0.0, 2.0, 0.0]]))
    assert u[0] == pytest.approx(CX)
    assert v[0] == pytest.approx(CY)
    assert depth[0] == pytest.approx(2.0)


def test_higher_in_the_room_is_higher_in_the_image():
    """ARKit +Y is up and image rows count downward, so raising a point must
    lower its row. This is the assertion a flipped sign fails."""
    _, level, _ = camera().project(np.array([[0.0, 2.0, 0.0]]))
    _, raised, _ = camera().project(np.array([[0.0, 2.0, 1.0]]))
    assert raised[0] < level[0]


def test_right_of_the_camera_is_right_in_the_image():
    ahead, _, _ = camera().project(np.array([[0.0, 2.0, 0.0]]))
    right, _, _ = camera().project(np.array([[1.0, 2.0, 0.0]]))
    assert right[0] > ahead[0]


def test_a_point_behind_the_camera_reports_no_depth():
    _, _, depth = camera().project(np.array([[0.0, -2.0, 0.0]]))
    assert depth[0] <= 0


def test_depth_is_distance_along_the_view_direction():
    _, _, depth = camera().project(np.array([[0.0, 5.0, 0.0]]))
    assert depth[0] == pytest.approx(5.0)


def test_the_floor_drop_lands_the_camera_above_the_floor():
    """RoomPlan's origin is wherever the phone started, about chest height, so a
    real capture reports its floor near ARKit y = -1.4. The room frame lifts
    everything so that floor is z = 0, which puts the camera 1.4 m up."""
    assert camera(floor_height=-1.4).position[2] == pytest.approx(1.4)


def test_a_point_on_the_floor_appears_below_the_centre():
    """The camera stands above the floor, so the floor is in the lower half of
    the picture: 1.4 m down at 2 m away, through a 1000 px focal length."""
    _, v, depth = camera(floor_height=-1.4).project(np.array([[0.0, 2.0, 0.0]]))
    assert depth[0] == pytest.approx(2.0)
    assert v[0] == pytest.approx(CY + FY * 1.4 / 2.0)


def test_the_floor_drop_is_applied_exactly_once():
    """The same physical point measures the same depth whatever the floor
    height, because the drop moves the camera and the point together."""
    _, _, at_origin = camera(floor_height=0.0).project(np.array([[0.0, 2.0, 0.0]]))
    _, _, dropped = camera(floor_height=-1.4).project(np.array([[0.0, 2.0, 1.4]]))
    assert at_origin[0] == pytest.approx(dropped[0])


def test_the_principal_ray_survives_resizing():
    """The stored JPEG is smaller than the calibration resolution. Rescaling
    moves the principal point but must not move what it points at."""
    full = camera_from_pose(pose(), capture_to_room(0.0))
    half = camera_from_pose(
        pose(image_size=(960, 720)), capture_to_room(0.0)
    )
    ahead = np.array([[0.0, 2.0, 0.0]])
    u_full, v_full, _ = full.project(ahead)
    u_half, v_half, _ = half.project(ahead)
    assert (u_full[0] + 0.5) / 2 - 0.5 == pytest.approx(u_half[0])
    assert (v_full[0] + 0.5) / 2 - 0.5 == pytest.approx(v_half[0])


def test_resizing_scales_the_focal_length():
    half = camera_from_pose(pose(image_size=(960, 720)), capture_to_room(0.0))
    assert half.fx == pytest.approx(FX / 2)
    assert half.fy == pytest.approx(FY / 2)


def test_the_camera_knows_where_it_stands():
    assert camera().position == pytest.approx(np.zeros(3), abs=1e-9)


def test_a_moved_camera_knows_where_it_stands():
    """ARKit translation sits in the fourth column of a column-major matrix."""
    moved = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 1.0, 2.0, 3.0, 1]
    placed = camera_from_pose(pose(transform=moved), capture_to_room(0.0))
    assert placed.position == pytest.approx(np.array([1.0, -3.0, 2.0]), abs=1e-9)


def test_a_version_one_record_is_refused():
    """Older captures have no image metadata, so they cannot be projected and
    must say so rather than guess a resolution."""
    old = PoseRecord(
        image="frames/frame_0000.jpg",
        timestamp=0.0,
        transform=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        intrinsics=[FX, 0, 0, 0, FY, 0, CX, CY, 1],
    )
    with pytest.raises(CameraMetadataError):
        camera_from_pose(old, capture_to_room(0.0))
