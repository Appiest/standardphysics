"""Photo evidence for a labelling request: calibrated object crops when poses prove them, whole frames otherwise."""

from __future__ import annotations

import base64
import io
import pathlib
from dataclasses import dataclass
from typing import Any, Iterable, Sequence
from uuid import UUID

import numpy as np
from standardphysics_contracts import PoseRecord, SceneGraph, SceneNode

from .astra_frames import MAX_IMAGE_COUNT, frame_key_of, load_poses, select_keyframes, unique_paths
from .textures.camera import CameraMetadataError, camera_from_pose

MAX_IMAGE_DIMENSION = 1024
MAX_IMAGE_BYTES = 2_000_000
MAX_SOURCE_IMAGE_BYTES = 16_000_000
MAX_SOURCE_IMAGE_PIXELS = 40_000_000
IMAGE_JPEG_QUALITIES = (82, 72, 62, 52)


@dataclass(frozen=True)
class CalibratedEvidence:
    frame_id: str
    object_ids: tuple[UUID, ...]
    camera_local: tuple[float, float, float]
    message: dict[str, Any]


Candidate = tuple[float, str, pathlib.Path, tuple[int, int, int, int], str, tuple[float, ...], tuple[float, ...]]
Selection = tuple[UUID, str, pathlib.Path, tuple[int, int, int, int], str, tuple[float, float, float]]


def _crop_candidates(graph: SceneGraph, paths: list[pathlib.Path], poses_path) -> dict[UUID, list[Candidate]]:
    """Every object crop a calibrated pose can prove, keyed by object."""
    path_by_id = {frame_key_of(path.name): path for path in paths}
    candidates: dict[UUID, list[Candidate]] = {}
    dimensions_match: dict[pathlib.Path, bool] = {}
    objects = graph.contents()
    for pose in load_poses(poses_path):
        record = pose.record
        path = path_by_id.get(pose.frame_key)
        if record is None or not record.projectable or path is None:
            continue
        if path not in dimensions_match:
            dimensions_match[path] = _matches_pose_image(path, record)
        if not dimensions_match[path]:
            continue
        try:
            camera = camera_from_pose(record, graph.capture_to_room)
        except (CameraMetadataError, ValueError, np.linalg.LinAlgError):
            continue
        _collect_crops(objects, camera, record, path, candidates)
    return candidates


def _collect_crops(objects, camera, record, path: pathlib.Path, candidates: dict[UUID, list[Candidate]]) -> None:
    for node in objects:
        crop, score = projected_object_crop(node, camera)
        if crop is None:
            continue
        candidates.setdefault(node.id, []).append((
            score, record.frame_id, path, crop, record.orientation,
            tuple(float(value) for value in camera.position),
            tuple(float(value) for value in camera.forward),
        ))


def _best_views(graph: SceneGraph, candidates: dict[UUID, list[Candidate]]) -> list[Selection]:
    """The strongest crop per object, plus one view from a different angle.

    A second crop only earns its place when it sees the object from somewhere
    else; two frames of the same angle prove nothing the first did not.
    """
    selected: list[Selection] = []
    for node in graph.contents():
        ranked = sorted(candidates.get(node.id, []), key=lambda item: (-item[0], item[1]))
        if ranked:
            _, frame_id, path, crop, orientation, position, _ = ranked[0]
            selected.append((node.id, frame_id, path, crop, orientation, _camera_local(node, position)))
            alternate = _complementary_of(ranked, frame_id)
            if alternate is not None:
                _, other_id, other_path, other_crop, other_orientation, other_position, _ = alternate
                selected.append(
                    (node.id, other_id, other_path, other_crop, other_orientation,
                     _camera_local(node, other_position))
                )
        if len(selected) >= MAX_IMAGE_COUNT:
            break
    return selected


def _complementary_of(ranked: list[Candidate], frame_id: str) -> Candidate | None:
    for candidate in ranked[1:]:
        if candidate[1] != frame_id and _complementary_view(ranked[0], candidate):
            return candidate
    return None


def _encoded_evidence(selected: list[Selection]) -> list[CalibratedEvidence]:
    evidence: list[CalibratedEvidence] = []
    for node_id, frame_id, path, crop, orientation, camera_local in selected:
        encoded = encode_crop(path, crop, orientation)
        if encoded is None:
            continue
        evidence.append(CalibratedEvidence(
            frame_id=frame_id,
            object_ids=(node_id,),
            camera_local=camera_local,
            message={
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")},
            },
        ))
    return evidence


def calibrated_photo_evidence(
    graph: SceneGraph,
    frame_paths: Iterable[pathlib.Path] | None,
    poses_path: pathlib.Path | None,
) -> list[CalibratedEvidence]:
    """Create bounded object crops only when calibrated metadata proves the association.

    A full frame can help labels, but it does not establish that a particular
    measured object was observed.  Completion evidence therefore comes only
    from a camera projection through the graph's capture-to-room transform.
    """
    if graph.capture_to_room is None or not frame_paths:
        return []
    candidates = _crop_candidates(graph, unique_paths(frame_paths), poses_path)
    return _encoded_evidence(_best_views(graph, candidates))


def _matches_pose_image(path: pathlib.Path, record: PoseRecord) -> bool:
    """A calibrated crop is valid only for the exact pixel dimensions it was posed for."""
    try:
        from PIL import Image
        with Image.open(path) as image:
            return (image.width, image.height) == (record.image_width, record.image_height)
    except (ImportError, OSError, TypeError, ValueError):
        return False


def _complementary_view(
    first: tuple[Any, ...], second: tuple[Any, ...]
) -> bool:
    """A second crop must add a materially different camera view, never a duplicate."""
    first_position, first_forward = np.asarray(first[5]), np.asarray(first[6])
    second_position, second_forward = np.asarray(second[5]), np.asarray(second[6])
    distance = float(np.linalg.norm(second_position - first_position))
    direction_dot = float(np.dot(first_forward, second_forward))
    return distance >= 0.25 or direction_dot <= 0.94


def _camera_local(node: SceneNode, position: Sequence[float]) -> tuple[float, float, float]:
    """The crop camera in normalized object-local coordinates, bounded for prompts."""
    try:
        inverse = np.linalg.inv(np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4))
        local = inverse @ np.asarray([*position, 1.0], dtype=np.float64)
        normalized = local[:3] / np.asarray(node.dimensions.as_tuple(), dtype=np.float64)
        bounded = np.clip(normalized, -20.0, 20.0)
        x, y, z = (float(round(value, 3)) for value in bounded)
        return x, y, z
    except (TypeError, ValueError, np.linalg.LinAlgError):
        return (0.0, 0.0, 0.0)


def projected_object_crop(node: SceneNode, camera: Any) -> tuple[tuple[int, int, int, int] | None, float]:
    half = (node.dimensions.x / 2, node.dimensions.y / 2, node.dimensions.z / 2)
    points = np.asarray([
        _node_point(node, sx * half[0], sy * half[1], sz * half[2])
        for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)
    ], dtype=np.float64)
    pixel_x, pixel_y, depth = camera.project(points)
    if not bool(np.all(depth > 0.10)):
        return None, 0.0
    center = node.transform.position
    center_x, center_y, center_depth = camera.project(np.asarray([[center.x, center.y, center.z]], dtype=np.float64))
    if center_depth[0] <= 0.10 or not (0 <= center_x[0] <= camera.width and 0 <= center_y[0] <= camera.height):
        return None, 0.0
    left, right = float(np.min(pixel_x)), float(np.max(pixel_x))
    top, bottom = float(np.min(pixel_y)), float(np.max(pixel_y))
    raw_area = max(0.0, right - left) * max(0.0, bottom - top)
    if raw_area <= 0:
        return None, 0.0
    visible_left, visible_right = max(0.0, left), min(float(camera.width), right)
    visible_top, visible_bottom = max(0.0, top), min(float(camera.height), bottom)
    visible_area = max(0.0, visible_right - visible_left) * max(0.0, visible_bottom - visible_top)
    coverage = visible_area / raw_area
    if coverage < 0.85 or raw_area > camera.width * camera.height * 0.70:
        return None, 0.0
    pad_x, pad_y = max(8.0, (right - left) * 0.12), max(8.0, (bottom - top) * 0.12)
    left, right = max(0.0, left - pad_x), min(float(camera.width), right + pad_x)
    top, bottom = max(0.0, top - pad_y), min(float(camera.height), bottom + pad_y)
    if right - left < 24 or bottom - top < 24:
        return None, 0.0
    area = (right - left) * (bottom - top)
    centeredness = 1.0 - min(1.0, abs(center_x[0] - camera.width / 2) / (camera.width / 2) + abs(center_y[0] - camera.height / 2) / (camera.height / 2)) / 2
    score = coverage * centeredness * area / max(float(np.mean(depth)), 0.1)
    return (round(left), round(top), round(right), round(bottom)), score


def _node_point(node: SceneNode, x: float, y: float, z: float) -> tuple[float, float, float]:
    matrix = node.transform.m
    return (
        matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
        matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
        matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11],
    )


def encode_crop(path: pathlib.Path, crop: tuple[int, int, int, int], orientation: str) -> bytes | None:
    try:
        source = pathlib.Path(path)
        if source.stat().st_size > MAX_SOURCE_IMAGE_BYTES:
            return None
        from PIL import Image, ImageOps
        with Image.open(source) as opened:
            if opened.width * opened.height > MAX_SOURCE_IMAGE_PIXELS:
                return None
            image = ImageOps.exif_transpose(opened).convert("RGB")
        rotate_portrait = orientation.startswith("portrait") and image.width > image.height
        rotate_landscape = orientation.startswith("landscape") and image.height > image.width
        image = image.crop(crop)
        if rotate_portrait:
            image = image.transpose(Image.Transpose.ROTATE_270)
        elif rotate_landscape:
            image = image.transpose(Image.Transpose.ROTATE_90)
        image.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS)
        for quality in IMAGE_JPEG_QUALITIES:
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            encoded = output.getvalue()
            if len(encoded) <= MAX_IMAGE_BYTES:
                return encoded
    except (ImportError, OSError, TypeError, ValueError):
        return None
    return None


def photo_evidence_context(evidence: Sequence[CalibratedEvidence]) -> list[dict[str, Any]]:
    return [
        {
            "frame_id": item.frame_id,
            "object_ids": [str(node_id) for node_id in item.object_ids],
            "camera_local": list(item.camera_local),
        }
        for item in evidence
    ]


def frame_image_messages(
    graph: SceneGraph,
    frame_paths: Iterable[pathlib.Path] | None,
    poses_path: pathlib.Path | None,
) -> list[dict[str, Any]]:
    if not frame_paths:
        return []
    paths = select_keyframes(graph, frame_paths, poses_path)
    pose_by_key = {pose.frame_key: pose for pose in load_poses(poses_path)}
    messages: list[dict[str, Any]] = []
    total_bytes = 0
    for path in paths:
        pose = pose_by_key.get(frame_key_of(path.name))
        encoded = _encode_frame(path, pose.orientation if pose else "")
        if encoded is None or total_bytes + len(encoded) > MAX_IMAGE_BYTES:
            continue
        total_bytes += len(encoded)
        messages.append({
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")},
        })
    return messages


def _encode_frame(path: pathlib.Path, orientation: str) -> bytes | None:
    """Validate and normalize an uploaded frame before embedding its bytes."""
    try:
        source = pathlib.Path(path)
        if source.stat().st_size > MAX_SOURCE_IMAGE_BYTES:
            return None
        raw = source.read_bytes()
    except (OSError, TypeError, ValueError):
        return None
    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(raw)) as opened:
            if opened.width * opened.height > MAX_SOURCE_IMAGE_PIXELS:
                return None
            opened.verify()
        with Image.open(io.BytesIO(raw)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
        if orientation.startswith("portrait") and image.width > image.height:
            image = image.transpose(Image.Transpose.ROTATE_270)
        elif orientation.startswith("landscape") and image.height > image.width:
            image = image.transpose(Image.Transpose.ROTATE_90)
        image.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS)
        for quality in IMAGE_JPEG_QUALITIES:
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            encoded = output.getvalue()
            if len(encoded) <= MAX_IMAGE_BYTES:
                return encoded
    except (ImportError, OSError, TypeError, ValueError):
        return None
    return None
