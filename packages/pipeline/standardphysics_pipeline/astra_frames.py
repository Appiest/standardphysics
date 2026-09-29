"""Choosing which uploaded frames to show the model: pose metadata, and how well each pose sees a measured object."""

from __future__ import annotations

import json
import math
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np
from standardphysics_contracts import PoseRecord, SceneGraph, SceneNode

from .textures.camera import CameraMetadataError, camera_from_pose

MAX_IMAGE_COUNT = 6
_FRAME_NUMBER = re.compile(r"frame[-_](\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class PoseEvidence:
    frame_key: str
    orientation: str
    transform: tuple[float, ...]
    intrinsics: tuple[float, ...]
    order: int
    record: PoseRecord | None = None


@dataclass(frozen=True)
class _FrameCandidate:
    path: pathlib.Path
    score: float
    in_view: bool
    order: int


def select_keyframes(
    graph: SceneGraph,
    frame_paths: Iterable[pathlib.Path],
    poses_path: pathlib.Path | None = None,
    *,
    max_images: int = MAX_IMAGE_COUNT,
) -> list[pathlib.Path]:
    """Choose a small, deterministic set of uploaded frames for label evidence.

    When pose metadata is available, candidates are ranked by the projection of
    each object's measured centre into the camera.  A round-robin pass gives an
    object more than one view when the request has room for it; a final pass
    fills any remaining slots with the strongest candidates.  Missing or bad
    metadata falls back to evenly spaced paths, so labels still work for older
    uploads that contain only a few frame artifacts.
    """
    limit = max(0, min(int(max_images), MAX_IMAGE_COUNT))
    paths = unique_paths(frame_paths)
    if limit == 0 or not paths:
        return []
    poses = load_poses(poses_path)
    if not poses:
        return _spread_paths(paths, limit)

    pose_by_key = {pose.frame_key: pose for pose in poses}
    candidates = [
        _FrameCandidate(path, score, visible, order)
        for order, path in enumerate(paths)
        for score, visible in [_best_frame_score(path, graph, pose_by_key)]
    ]
    objects = graph.contents()
    if not objects:
        return _strongest_paths(candidates, limit)

    rankings = [_rank_for_object(node, paths, pose_by_key, graph.capture_to_room) for node in objects]
    # Two views are useful for one or two objects. For a larger graph, give each
    # object one view first and use the remaining slots for coverage.
    views_per_object = 2 if len(objects) <= 3 else 1
    selected = _round_robin_views(rankings, views_per_object, limit)
    if len(selected) >= limit:
        return selected[:limit]
    return _fill_remaining(candidates, selected, limit)


def _round_robin_views(
    rankings: list[list["_FrameCandidate"]], views_per_object: int, limit: int
) -> list[pathlib.Path]:
    """One frame per object per pass, so no single object eats the whole budget."""
    selected: list[pathlib.Path] = []
    seen: set[pathlib.Path] = set()
    for view_number in range(views_per_object):
        for ranked in rankings:
            for candidate in ranked[view_number:]:
                if candidate.path not in seen:
                    selected.append(candidate.path)
                    seen.add(candidate.path)
                    break
            if len(selected) >= limit:
                return selected
    return selected


def _fill_remaining(
    candidates: list["_FrameCandidate"], selected: list[pathlib.Path], limit: int
) -> list[pathlib.Path]:
    """Spend whatever budget the round robin left on the strongest frames."""
    filled = list(selected)
    seen = set(filled)
    for candidate in sorted(candidates, key=lambda item: (-item.score, item.order)):
        if len(filled) >= limit:
            break
        if candidate.path not in seen:
            filled.append(candidate.path)
            seen.add(candidate.path)
    return filled[:limit]


def unique_paths(frame_paths: Iterable[pathlib.Path]) -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    seen: set[pathlib.Path] = set()
    for raw_path in frame_paths or ():
        try:
            path = pathlib.Path(raw_path)
        except (TypeError, ValueError):
            continue
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def _spread_paths(paths: Sequence[pathlib.Path], limit: int) -> list[pathlib.Path]:
    if len(paths) <= limit:
        return list(paths)
    if limit == 1:
        return [paths[0]]
    indices = {
        round(index * (len(paths) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [paths[index] for index in sorted(indices)]


def _strongest_paths(candidates: Sequence[_FrameCandidate], limit: int) -> list[pathlib.Path]:
    ranked = sorted(candidates, key=lambda item: (-item.score, item.order))
    return [candidate.path for candidate in ranked[:limit]]


def _rank_for_object(
    node: SceneNode,
    paths: Sequence[pathlib.Path],
    pose_by_key: dict[str, PoseEvidence],
    capture_to_room: Any = None,
) -> list[_FrameCandidate]:
    ranked: list[_FrameCandidate] = []
    for order, path in enumerate(paths):
        pose = pose_by_key.get(frame_key_of(path.name))
        if pose is None:
            ranked.append(_FrameCandidate(path, -100.0, False, order))
            continue
        score, visible = project_score(node, pose, capture_to_room)
        ranked.append(_FrameCandidate(path, score, visible, order))
    in_view = [candidate for candidate in ranked if candidate.in_view]
    pool = in_view or ranked
    return sorted(pool, key=lambda item: (-item.score, item.order))


def _best_frame_score(
    path: pathlib.Path,
    graph: SceneGraph,
    pose_by_key: dict[str, PoseEvidence],
) -> tuple[float, bool]:
    pose = pose_by_key.get(frame_key_of(path.name))
    if pose is None:
        return -100.0, False
    scores = [project_score(node, pose, graph.capture_to_room) for node in graph.contents()]
    if not scores:
        return 0.0, False
    return max(scores, key=lambda item: item[0])


def frame_key_of(value: str) -> str:
    match = _FRAME_NUMBER.search(pathlib.Path(value).name)
    if match:
        return f"frame-{int(match.group(1))}"
    return pathlib.Path(value).stem.casefold()


def load_poses(poses_path: pathlib.Path | None) -> list[PoseEvidence]:
    if poses_path is None:
        return []
    try:
        payload = json.loads(pathlib.Path(poses_path).read_bytes())
    except (OSError, TypeError, ValueError, UnicodeDecodeError):
        return []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        possible = payload.get("poses") or payload.get("frames")
        items = possible if isinstance(possible, list) else []
    else:
        items = []
    poses: list[PoseEvidence] = []
    for order, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("image"), str):
            continue
        transform = _numbers(item.get("transform"), 16)
        intrinsics = _numbers(item.get("intrinsics"), 9)
        if transform is None:
            continue
        poses.append(PoseEvidence(
            frame_key=frame_key_of(item["image"]),
            orientation=str(item.get("orientation") or "").casefold(),
            transform=transform,
            intrinsics=intrinsics or (),
            order=order,
            record=_pose_record(item),
        ))
    return poses


def _numbers(value: object, length: int) -> tuple[float, ...] | None:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        return None
    try:
        numbers = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    return numbers if all(math.isfinite(item) for item in numbers) else None


def _pose_record(item: dict[str, Any]) -> PoseRecord | None:
    try:
        return PoseRecord.model_validate(item)
    except (TypeError, ValueError):
        return None


def _calibrated_score(node: SceneNode, record: PoseRecord, capture_to_room: Any) -> tuple[float, bool] | None:
    try:
        camera = camera_from_pose(record, capture_to_room)
        point = node.transform.position
        pixel_x, pixel_y, depth = camera.project(np.array([[point.x, point.y, point.z]]))
        if depth[0] <= 0.05:
            return -float(abs(depth[0])), False
        margin_x, margin_y = camera.width * 0.25, camera.height * 0.25
        in_view = -margin_x <= pixel_x[0] <= camera.width + margin_x and -margin_y <= pixel_y[0] <= camera.height + margin_y
        offset = abs(pixel_x[0] - camera.cx) / max(camera.width / 2, 1) + abs(pixel_y[0] - camera.cy) / max(camera.height / 2, 1)
        return (10.0 if in_view else -4.0) - offset - float(depth[0]) * 0.02, in_view
    except (CameraMetadataError, ValueError, np.linalg.LinAlgError):
        return None


def project_score(
    node: SceneNode, pose: PoseEvidence, capture_to_room: Any = None
) -> tuple[float, bool]:
    if pose.record is not None and pose.record.projectable and capture_to_room is not None:
        calibrated = _calibrated_score(node, pose.record, capture_to_room)
        if calibrated is not None:
            return calibrated
    matrix = pose.transform
    if len(matrix) != 16 or not all(math.isfinite(value) for value in matrix):
        return -100.0, False
    position = node.transform.position
    # Pose metadata stays in ARKit's Y-up, column-major coordinates.  Convert
    # the measured point back to that frame and use the camera basis directly;
    # conjugating the matrix would rotate the camera's local axes a second
    # time.  ARKit cameras look down local -Z.
    world = _room_to_capture(position.x, position.y, position.z, capture_to_room)
    origin = (matrix[12], matrix[13], matrix[14])
    delta = (world[0] - origin[0], world[1] - origin[1], world[2] - origin[2])
    right = (matrix[0], matrix[1], matrix[2])
    up = (matrix[4], matrix[5], matrix[6])
    backward = (matrix[8], matrix[9], matrix[10])
    local_x = sum(delta[index] * right[index] for index in range(3))
    local_y = sum(delta[index] * up[index] for index in range(3))
    local_z = sum(delta[index] * backward[index] for index in range(3))
    depth = -local_z
    distance = math.sqrt(sum(value * value for value in delta))
    if depth <= 0.05 or distance <= 0.05:
        return -distance, False

    fx = pose.intrinsics[0] if len(pose.intrinsics) >= 5 and pose.intrinsics[0] > 0 else 1000.0
    fy = pose.intrinsics[4] if len(pose.intrinsics) >= 5 and pose.intrinsics[4] > 0 else fx
    cx = pose.intrinsics[6] if len(pose.intrinsics) >= 8 and pose.intrinsics[6] > 0 else 960.0
    cy = pose.intrinsics[7] if len(pose.intrinsics) >= 8 and pose.intrinsics[7] > 0 else 720.0
    width, height = max(2.0 * cx, 1.0), max(2.0 * cy, 1.0)
    projected_x = fx * local_x / depth + cx
    projected_y = fy * local_y / depth + cy
    margin_x, margin_y = width * 0.25, height * 0.25
    in_view = (
        -margin_x <= projected_x <= width + margin_x
        and -margin_y <= projected_y <= height + margin_y
    )
    center_offset = abs(projected_x - cx) / (width / 2) + abs(projected_y - cy) / (height / 2)
    alignment = min(1.0, depth / distance)
    score = (10.0 if in_view else -4.0) + alignment * 4.0 - center_offset - distance * 0.02
    return score, in_view


def _room_to_capture(x: float, y: float, z: float, capture_to_room: Any) -> tuple[float, float, float]:
    """Map a measured room point into ARKit world for legacy pose ranking."""
    values = getattr(capture_to_room, "m", None)
    if not isinstance(values, list) or len(values) != 16:
        return x, z, -y
    try:
        inverse = np.linalg.inv(np.asarray(values, dtype=np.float64).reshape(4, 4))
        result = inverse @ np.asarray([x, y, z, 1.0], dtype=np.float64)
        if not np.all(np.isfinite(result)) or abs(result[3]) < 1e-9:
            return x, z, -y
        return tuple((result[:3] / result[3]).tolist())
    except (TypeError, ValueError, np.linalg.LinAlgError):
        return x, z, -y
