"""A photo of a known board, rendered from ARKit's conventions alone, must project back onto the board.

The renderer below never touches `standardphysics_pipeline.textures.camera`. It
casts rays the way ARKit defines them: intrinsics at calibration resolution,
column-major camera-to-world, camera looking down -Z with +Y at the top of the
sensor image, Y up in the world, floor below the phone. Each pixel stores the
room X and Z it saw plus a checker bit, so projecting a room point and reading
the pixel back exposes a mirrored axis, a swapped axis, a wrong scale, a
half-pixel shift or a missing floor offset as a coordinate mismatch.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from standardphysics_contracts import PoseRecord
from standardphysics_pipeline.coords import capture_to_room
from standardphysics_pipeline.textures.camera import CameraMetadataError, PhotoCamera, camera_from_pose, load_cameras
from standardphysics_pipeline.textures.project import DepthBuffers, rasterize_atlas, triangle_depth_buffer, view_samples

FLOOR_HEIGHT = -1.3
CALIBRATION = (1920, 1440)
ENCODED = (480, 360)
INTRINSICS = (1350.0, 1350.0, 961.5, 723.0)
BOARD_Y = 2.5
BOARD_X = (-1.5, 1.5)
BOARD_Z = (0.2, 2.4)
SQUARE = 0.25


def rotation(axis: str, degrees: float) -> np.ndarray:
    angle = np.radians(degrees)
    c, s = np.cos(angle), np.sin(angle)
    return {
        "x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]),
        "y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
        "z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]),
    }[axis]


def arkit_camera_to_world() -> np.ndarray:
    """A phone held in portrait, tilted and turned, standing off-centre. ARKit -Z is room +Y."""
    portrait = rotation("z", -90.0)
    tilt = rotation("x", 8.0)
    yaw = rotation("y", 12.0)
    matrix = np.eye(4)
    matrix[:3, :3] = yaw @ portrait @ tilt
    matrix[:3, 3] = [0.3, 0.0, 0.0]
    return matrix


def room_from_arkit(points: np.ndarray) -> np.ndarray:
    return np.stack([points[..., 0], -points[..., 2], points[..., 1] - FLOOR_HEIGHT], axis=-1)


def render_board(camera_to_world: np.ndarray) -> np.ndarray:
    fx, fy, cx, cy = INTRINSICS
    columns, rows = np.meshgrid(np.arange(ENCODED[0]), np.arange(ENCODED[1]))
    calibration_u = (columns + 0.5) * CALIBRATION[0] / ENCODED[0] - 0.5
    calibration_v = (rows + 0.5) * CALIBRATION[1] / ENCODED[1] - 0.5
    rays = np.stack([(calibration_u - cx) / fx, -(calibration_v - cy) / fy, -np.ones_like(calibration_u)], axis=-1)
    world_rays = rays @ camera_to_world[:3, :3].T
    origin = camera_to_world[:3, 3]
    board_world_z = -BOARD_Y
    distance = (board_world_z - origin[2]) / world_rays[..., 2]
    hits = room_from_arkit(origin + distance[..., None] * world_rays)
    on_board = (distance > 0) & between(hits[..., 0], BOARD_X) & between(hits[..., 2], BOARD_Z)
    checker = (np.floor(hits[..., 0] / SQUARE) + np.floor(hits[..., 2] / SQUARE)) % 2
    return np.where(on_board[..., None], np.stack([hits[..., 0], hits[..., 2], checker], axis=-1), np.nan)


def between(values: np.ndarray, bounds: tuple[float, float]) -> np.ndarray:
    return (values >= bounds[0]) & (values <= bounds[1])


def pose_record(camera_to_world: np.ndarray, **overrides) -> PoseRecord:
    fx, fy, cx, cy = INTRINSICS
    fields = dict(
        metadata_version=2, frame_id="frame-0007", image="frames/frame_0007.jpg", timestamp=1.0,
        transform=camera_to_world.T.reshape(-1).tolist(),
        intrinsics=[fx, 0, 0, 0, fy, 0, cx, cy, 1],
        orientation="portrait",
        image_width=ENCODED[0], image_height=ENCODED[1],
        calibration_width=CALIBRATION[0], calibration_height=CALIBRATION[1],
        image_orientation="sensor",
    )
    return PoseRecord(**{**fields, **overrides})


def board_points(count: int) -> np.ndarray:
    generator = np.random.default_rng(4)
    return np.stack([
        generator.uniform(*BOARD_X, count),
        np.full(count, BOARD_Y),
        generator.uniform(*BOARD_Z, count),
    ], axis=-1)


def test_every_board_pixel_reprojects_onto_its_own_centre():
    camera_to_world = arkit_camera_to_world()
    image = render_board(camera_to_world)
    camera = camera_from_pose(pose_record(camera_to_world), capture_to_room(FLOOR_HEIGHT))
    rows, columns = np.nonzero(~np.isnan(image[..., 0]))
    seen = image[rows, columns]
    points = np.stack([seen[:, 0], np.full(len(seen), BOARD_Y), seen[:, 1]], axis=-1)

    u, v, depth = camera.project(points)

    assert len(points) > 0.5 * ENCODED[0] * ENCODED[1], "the board should fill most of the frame"
    assert np.all(depth > 0)
    assert np.abs(u - columns).max() < 1e-6
    assert np.abs(v - rows).max() < 1e-6


def test_checker_squares_agree_away_from_edges():
    camera_to_world = arkit_camera_to_world()
    image = render_board(camera_to_world)
    camera = camera_from_pose(pose_record(camera_to_world), capture_to_room(FLOOR_HEIGHT))
    points = board_points(4000)
    offset = np.stack([points[:, 0] / SQUARE, points[:, 2] / SQUARE]) % 1
    interior = np.all((offset > 0.2) & (offset < 0.8), axis=0)
    expected = (np.floor(points[:, 0] / SQUARE) + np.floor(points[:, 2] / SQUARE)) % 2

    u, v, depth = camera.project(points[interior])
    columns, rows = np.rint(u).astype(int), np.rint(v).astype(int)
    inside = (depth > 0) & between(columns, (0, ENCODED[0] - 1)) & between(rows, (0, ENCODED[1] - 1))
    seen = image[rows[inside], columns[inside], 2]

    assert np.array_equal(seen, expected[interior][inside])


def test_camera_position_and_facing_are_in_the_room_frame():
    camera_to_world = arkit_camera_to_world()
    camera = camera_from_pose(pose_record(camera_to_world), capture_to_room(FLOOR_HEIGHT))

    assert np.allclose(camera.position, [0.3, 0.0, -FLOOR_HEIGHT])
    assert camera.forward[1] > 0.9, "the phone faces the board at room +Y"


def test_version_one_records_cannot_be_projected(tmp_path):
    camera_to_world = arkit_camera_to_world()
    legacy = pose_record(camera_to_world).model_dump()
    for field in ("metadata_version", "frame_id", "image_width", "image_height",
                  "calibration_width", "calibration_height", "image_orientation"):
        legacy.pop(field)
    with pytest.raises(CameraMetadataError):
        camera_from_pose(PoseRecord.model_validate(legacy), capture_to_room(FLOOR_HEIGHT))

    poses = tmp_path / "poses.json"
    poses.write_text(json.dumps([legacy]))
    with pytest.raises(CameraMetadataError):
        load_cameras(poses, ["frame-0007"], capture_to_room(FLOOR_HEIGHT))


def test_load_cameras_keeps_only_requested_projectable_frames(tmp_path):
    camera_to_world = arkit_camera_to_world()
    records = [
        pose_record(camera_to_world, frame_id="frame-0002", image="frames/frame_0002.jpg", timestamp=2.0).model_dump(),
        pose_record(camera_to_world, frame_id="frame-0001", image="frames/frame_0001.jpg", timestamp=1.0).model_dump(),
        pose_record(camera_to_world, frame_id="frame-0003", image="frames/frame_0003.jpg", timestamp=3.0).model_dump(),
    ]
    poses = tmp_path / "poses.json"
    poses.write_text(json.dumps(records))

    cameras = load_cameras(poses, ["frame-0001", "frame-0002"], capture_to_room(FLOOR_HEIGHT))

    assert [camera.frame_id for camera in cameras] == ["frame-0001", "frame-0002"]


def test_triangle_depth_rejects_background_between_lidar_vertices():
    camera = PhotoCamera("frame-0001", np.eye(4), 32, 32, 31.5, 31.5, 64, 64, 1)
    foreground = np.array([[[-0.6, -0.6, 1.0], [0.6, -0.6, 1.0], [0.0, 0.6, 1.0]]])
    depth = triangle_depth_buffer(camera, foreground)
    positions = np.array([[0.0, 0.0, 2.0]], dtype=np.float32)
    normals = np.array([[0.0, 0.0, -1.0]], dtype=np.float32)

    samples = view_samples(DepthBuffers(camera, np.full_like(depth, np.inf), depth), positions, normals, 1.0)

    assert not samples.accepted[0]


def test_unknown_lidar_depth_keeps_photo_neutral():
    camera = PhotoCamera("frame-0001", np.eye(4), 32, 32, 31.5, 31.5, 64, 64, 1)
    unknown = np.full((8, 8), np.inf, dtype=np.float32)
    positions = np.array([[0.0, 0.0, 2.0]], dtype=np.float32)
    normals = np.array([[0.0, 0.0, -1.0]], dtype=np.float32)

    samples = view_samples(DepthBuffers(camera, unknown, unknown), positions, normals, 1.0)

    assert not samples.accepted[0]
    assert samples.disagreed[0]


def test_slanted_surface_cannot_bridge_a_30cm_foreground_depth_edge():
    """One reduced-buffer pixel is already represented in the tolerance."""
    camera = PhotoCamera("frame-0001", np.eye(4), 1000, 1000, 499.5, 499.5, 1000, 1000, 1)
    clean = np.full((125, 125), 4.7, dtype=np.float32)
    positions = np.array([[0.0, 0.0, 5.0]], dtype=np.float32)
    # The view angle is about 63 degrees from the surface normal (slope = 2).
    normals = np.array([[np.sqrt(0.8), 0.0, -np.sqrt(0.2)]], dtype=np.float32)

    samples = view_samples(DepthBuffers(camera, clean, None), positions, normals, 1.0)

    assert not samples.accepted[0]


def test_atlas_texels_keep_per_part_base_colours_for_uncovered_regions():
    world = np.array([
        [[0, 0, 1], [1, 0, 1], [0, 1, 1]],
        [[1, 0, 1], [1, 1, 1], [0, 1, 1]],
    ], dtype=np.float32)
    uv = np.array([
        [[0, 0], [0.5, 0], [0, 1]],
        [[0.5, 0], [1, 1], [0.5, 1]],
    ], dtype=np.float32)
    texels = rasterize_atlas(
        world, uv, np.array([0, 0]), 16,
        np.array([[0.4, 0.2, 0.1], [0.1, 0.2, 0.7]], dtype=np.float32),
    )

    assert np.any(np.all(np.isclose(texels.base_colours, [0.4, 0.2, 0.1]), axis=1))
    assert np.any(np.all(np.isclose(texels.base_colours, [0.1, 0.2, 0.7]), axis=1))
