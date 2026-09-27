"""Keyframe cameras expressed in the room frame, ready to project model points into photos.

Three conventions meet here and each is easy to get silently wrong.

**Poses.** ARKit writes the camera-to-world transform column-major, in its
Y-up world, with the camera looking down its local -Z and local +Y pointing to
the top of the sensor image. We turn that into a camera frame with +X right,
+Y down and +Z forward, where a pixel is (fx * x / z + cx, fy * y / z + cy).

**Room frame.** The measured model lives in the room frame `capture_to_room`
defines. A model point goes back through its inverse into ARKit world before the
camera sees it, so the floor drop and the axis turn are undone exactly once.

**Pixels.** Intrinsics describe the calibration resolution. The stored JPEG
may be smaller, so the focal lengths and principal point are rescaled with
pixel centres at integer coordinates, and the image is never rotated.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterable
from dataclasses import dataclass, replace

import numpy as np
from pydantic import ValidationError
from standardphysics_contracts import Mat4, PoseRecord

ARKIT_TO_PIXEL_AXES = np.diag([1.0, -1.0, -1.0, 1.0])


class CameraMetadataError(ValueError):
    pass


@dataclass(frozen=True)
class PhotoCamera:
    frame_id: str
    room_to_camera: np.ndarray
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int
    timestamp: float

    @property
    def position(self) -> np.ndarray:
        rotation, translation = self.room_to_camera[:3, :3], self.room_to_camera[:3, 3]
        return -rotation.T @ translation

    @property
    def forward(self) -> np.ndarray:
        return self.room_to_camera[2, :3].copy()

    def resized(self, width: int, height: int) -> PhotoCamera:
        scale_x, scale_y = width / self.width, height / self.height
        return replace(
            self,
            fx=self.fx * scale_x,
            fy=self.fy * scale_y,
            cx=(self.cx + 0.5) * scale_x - 0.5,
            cy=(self.cy + 0.5) * scale_y - 0.5,
            width=width,
            height=height,
        )

    def to_camera(self, points: np.ndarray) -> np.ndarray:
        """Camera-frame points, in single precision when the points are: a tenth of a millimetre across a library floor, and half the time and memory of doubles."""
        transform = self.room_to_camera.astype(np.float32) if points.dtype == np.float32 else self.room_to_camera
        return points @ transform[:3, :3].T + transform[:3, 3]

    def project(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Pixel column, pixel row and forward depth for room-frame points. Depth <= 0 is behind."""
        local = self.to_camera(points)
        depth = local[:, 2]
        safe = np.where(np.abs(depth) < 1e-9, 1e-9, depth)
        return self.fx * local[:, 0] / safe + self.cx, self.fy * local[:, 1] / safe + self.cy, depth


def camera_from_pose(pose: PoseRecord, capture_to_room: Mat4) -> PhotoCamera:
    if not pose.projectable:
        raise CameraMetadataError(f"{pose.frame_id or pose.image} lacks version 2 image metadata")
    camera_to_arkit = np.array(pose.transform, dtype=np.float64).reshape(4, 4).T
    room_to_arkit = np.linalg.inv(np.array(capture_to_room.m, dtype=np.float64).reshape(4, 4))
    room_to_camera = ARKIT_TO_PIXEL_AXES @ np.linalg.inv(camera_to_arkit) @ room_to_arkit
    k = pose.intrinsics
    calibrated = PhotoCamera(
        frame_id=pose.frame_id,
        room_to_camera=room_to_camera,
        fx=k[0], fy=k[4], cx=k[6], cy=k[7],
        width=pose.calibration_width, height=pose.calibration_height,
        timestamp=pose.timestamp,
    )
    return calibrated.resized(pose.image_width, pose.image_height)


def load_cameras(poses_path: pathlib.Path, frame_ids: Iterable[str], capture_to_room: Mat4) -> list[PhotoCamera]:
    """Cameras for the requested frames, in capture order. Records that cannot be projected are skipped."""
    wanted = set(frame_ids)
    cameras = [
        camera_from_pose(pose, capture_to_room)
        for pose in _pose_records(poses_path)
        if pose.frame_id in wanted and pose.projectable
    ]
    if not cameras:
        raise CameraMetadataError("no photo has version 2 camera metadata")
    return sorted(cameras, key=lambda camera: camera.timestamp)


def _pose_records(poses_path: pathlib.Path) -> list[PoseRecord]:
    try:
        payload = json.loads(pathlib.Path(poses_path).read_bytes())
    except (OSError, ValueError) as error:
        raise CameraMetadataError(f"unreadable poses: {error}") from error
    if not isinstance(payload, list):
        raise CameraMetadataError("poses must be a list")
    records = []
    for item in payload:
        try:
            records.append(PoseRecord.model_validate(item))
        except ValidationError:
            continue
    return records
