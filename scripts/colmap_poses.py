"""Read COLMAP cameras.bin/images.bin models into pose overrides.

Only the pose data is needed: camera_id -> coords of R, t column vectors (in
COLMAP's convention = world-to-camera), and image names -> camera pose ids.
The refinement trials these files come from already validated the manifest and
reported their reprojection improvement; this module just loads them.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np


def read_cameras_bin(path: Path) -> dict[int, np.ndarray]:
    with open(path, "rb") as f:
        count = struct.unpack("<Q", f.read(8))[0]
        cameras = {}
        for _ in range(count):
            camera_id, model_id, width, height = struct.unpack("<iiQQ", f.read(24))
            # COLMAP camera models store params positionally; only SIMPLE_PINHOLE (1),
            # PINHOLE (2) and OPENCV (4) appear here, all fx-first.
            params = struct.unpack("<4d", f.read(32))
            cameras[camera_id] = np.array(params, dtype=np.float64)
    return cameras


def read_images_bin(path: Path) -> dict[int, tuple[str, np.ndarray, np.ndarray]]:
    with open(path, "rb") as f:
        count = struct.unpack("<Q", f.read(8))[0]
        images = {}
        for _ in range(count):
            image_id, qw, qx, qy, qz, tx, ty, tz, camera_id = struct.unpack("<i4d3di", f.read(4 + 4 * 8 + 3 * 8 + 4))
            name_raw = f.read(1)
            while name_raw[-1:] != b"\x00":
                name_raw += f.read(1)
            name = name_raw[:-1].decode("utf-8", "replace")
            point_count = struct.unpack("<Q", f.read(8))[0]
            f.read(point_count * 24)
            q = np.array([qw, qx, qy, qz], dtype=np.float64)
            norm = np.linalg.norm(q)
            if norm > 0:
                q /= norm
            qw, qx, qy, qz = q
            rotation = np.array([
                [1 - 2 * (qy ** 2 + qz ** 2), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
                [2 * (qx * qy + qz * qw), 1 - 2 * (qx ** 2 + qz ** 2), 2 * (qy * qz - qx * qw)],
                [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx ** 2 + qy ** 2)],
            ], dtype=np.float64)
            translation = np.array([tx, ty, tz], dtype=np.float64)
            images[image_id] = (name, rotation, translation, camera_id)
    return images


def pose_overrides(model_dir: Path) -> dict[str, dict]:
    """frame-name -> {"R": world-to-camera 3x3, "t": (3,), "fx", "fy", "cx", "cy"}."""
    cameras = read_cameras_bin(model_dir / "cameras.bin")
    images = read_images_bin(model_dir / "images.bin")
    overrides = {}
    for _image_id, (name, rotation, translation, camera_id) in images.items():
        camera_params = cameras[camera_id]
        fx, fy, cx, cy = camera_params[0], camera_params[1], camera_params[2], camera_params[3]
        overrides[name] = {"R": rotation, "t": translation, "fx": float(fx), "fy": float(fy),
                           "cx": float(cx), "cy": float(cy)}
    return overrides
