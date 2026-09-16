"""Bounded Astra reconstruction for a measured RoomPlan graph.

The model may suggest labels and movability. It never changes geometry, node
identity, confirmation quality, or a lock already set by RoomPlan or an owner.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import math
import os
import pathlib
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Literal, Sequence
from uuid import UUID

import numpy as np
from standardphysics_contracts import (
    DisplayAppearance,
    DisplayReconstruction,
    PoseRecord,
    SceneGraph,
    SceneNode,
)

from .footprints import footprint, gap_between
from .ingest import FIXED_CATEGORIES
from .mesh_evidence import object_mesh_profiles
from .textures.camera import CameraMetadataError, camera_from_pose

logger = logging.getLogger(__name__)

API_KEY_ENV = "OPENROUTER_API_KEY"
MODEL_ENV = "OPENROUTER_MODEL"
BASE_URL_ENV = "OPENROUTER_BASE_URL"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-astra"
PROVIDER_ROUTING = {"order": ["openai"], "allow_fallbacks": False, "data_collection": "deny"}
REQUEST_TIMEOUT_SECONDS = 120.0
MAX_RECONSTRUCTION_WORKERS = 3
RECONSTRUCTION_WALL_TIMEOUT_SECONDS = 150.0

# A label request should carry enough visual evidence to disambiguate a box,
# without turning a four minute capture into a video upload.  These limits are
# applied after image validation and orientation normalization.
MAX_IMAGE_COUNT = 6
MAX_IMAGE_DIMENSION = 1024
MAX_IMAGE_BYTES = 2_000_000
MAX_SOURCE_IMAGE_BYTES = 16_000_000
MAX_SOURCE_IMAGE_PIXELS = 40_000_000
IMAGE_JPEG_QUALITIES = (82, 72, 62, 52)
_FRAME_NUMBER = re.compile(r"frame[-_](\d+)", re.IGNORECASE)
MAX_OUTPUT_TOKENS = 8_192
MAX_RESPONSE_BYTES = 2_000_000
# Each object can contribute two complementary crops; preserve the six-image
# request limit by asking about at most three objects per model call.
RECONSTRUCTION_BATCH_SIZE = MAX_IMAGE_COUNT // 2

QualityName = Literal["measured", "needs_another_look", "confirmed"]
ReconstructionSource = Literal["astra", "roomplan"]
Transport = Callable[[str, dict, dict[str, str]], dict]

COUNTER_LABELS = frozenset({"ordering counter", "service counter", "counter", "checkout counter", "cash wrap", "register counter", "sales counter"})
COUNTER_HEIGHT = (0.80, 1.40)
COUNTER_LENGTH = 2.2
WALL_GAP_METERS = 0.45
OVERLAP_METERS = 0.02
THIN_METERS = 0.04

INSTRUCTION = (
    "Label every supplied object in this shop scan with an ordinary name such as "
    "Ordering counter, Display case, Table, or Chair. Counters and plumbed-in "
    "fixtures are not movable. Do not change sizes. Mark thin or overlapping "
    "detections as needs_another_look. You may add a display-only appearance "
    "with a six-digit base color and broad material when the frame evidence is "
    "clear; when images_provided is false, appearance must be null. For each "
    "object, return reconstruction only when its calibrated photo crop evidence "
    "shows a recognizable assembly. Reconstruction parts are display-only, use "
    "only box/cylinder/ellipsoid primitives inside the normalized measured bounds, "
    "and must cite only frame ids associated with that object. Return every object "
    "exactly once. Parts use object-local XYZ = width, depth, height with Z up "
    "and local front at -Y; "
    "each axis must obey abs(center[i]) + size[i]/2 <= 0.5. Cylinder axis is its "
    "long direction, bevel is a fraction of the smallest part size. Complete "
    "missing legs and supports only as conservative plausible display inference; "
    "keep people, cables, and other clutter out. photo_evidence.camera_local is "
    "the crop camera in that object's normalized local XYZ: use it to orient the "
    "visible surfaces, rather than assuming a photo always faces local front. "
    "Prefer 6 to 12 meaningful parts."
)
_PART_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["name", "primitive", "center", "size", "axis", "bevel", "base_color", "material"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 60},
        "primitive": {"type": "string", "enum": ["box", "cylinder", "ellipsoid"]},
        "center": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number", "minimum": -0.5, "maximum": 0.5}},
        "size": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number", "exclusiveMinimum": 0, "maximum": 1}},
        "axis": {"type": "string", "enum": ["x", "y", "z"]},
        "bevel": {"type": "number", "minimum": 0, "maximum": 0.2},
        "base_color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
        "material": {"type": "string", "enum": ["paint", "wood", "fabric", "metal", "stone", "glass", "neutral"]},
    },
}
_RECONSTRUCTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "confidence", "evidence_frame_ids", "parts"],
    "properties": {
        "summary": {"type": "string", "minLength": 1, "maxLength": 300},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_frame_ids": {"type": "array", "minItems": 1, "maxItems": 6, "items": {"type": "string"}},
        "parts": {"type": "array", "minItems": 1, "maxItems": 32, "items": _PART_SCHEMA},
    },
}
LABEL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["nodes"],
    "properties": {"nodes": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "label", "movable", "quality", "appearance", "reconstruction"],
        "properties": {
            "id": {"type": "string"}, "label": {"type": "string"},
            "movable": {"type": "boolean"},
            "quality": {"type": "string", "enum": ["measured", "needs_another_look"]},
            "appearance": {"anyOf": [
                {"type": "null"},
                {
                    "type": "object", "additionalProperties": False,
                    "required": ["base_color", "material"],
                    "properties": {
                        "base_color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
                        "material": {"type": "string", "enum": ["paint", "wood", "fabric", "metal", "stone", "glass", "neutral"]},
                    },
                },
            ]},
            "reconstruction": {"anyOf": [{"type": "null"}, _RECONSTRUCTION_SCHEMA]},
        },
    }}},
}


@dataclass(frozen=True)
class LabelPatch:
    node_id: UUID
    label: str
    movable: bool
    quality: QualityName
    appearance: DisplayAppearance | None = None
    reconstruction: DisplayReconstruction | None = None


@dataclass(frozen=True)
class ReconstructionResult:
    """The graph plus the label source used for this reconstruction."""

    graph: SceneGraph
    source: ReconstructionSource

    @property
    def used_model(self) -> bool:
        return self.source == "astra"


def reconstruct(
    graph: SceneGraph,
    *,
    transport: Transport | None = None,
    frame_paths: Iterable[pathlib.Path] | None = None,
    poses_path: pathlib.Path | None = None,
    lidar_mesh_path: pathlib.Path | None = None,
) -> SceneGraph:
    """Return the safe reconstructed graph for pipeline callers."""
    return reconstruct_result(
        graph, transport=transport, frame_paths=frame_paths, poses_path=poses_path,
        lidar_mesh_path=lidar_mesh_path,
    ).graph


def reconstruct_result(
    graph: SceneGraph,
    *,
    transport: Transport | None = None,
    frame_paths: Iterable[pathlib.Path] | None = None,
    poses_path: pathlib.Path | None = None,
    lidar_mesh_path: pathlib.Path | None = None,
) -> ReconstructionResult:
    """Expose whether labels came from Astra or the deterministic local fallback."""
    remote = _remote_patches(
        graph, transport, frame_paths=frame_paths, poses_path=poses_path,
        lidar_mesh_path=lidar_mesh_path,
    )
    if remote is None:
        return ReconstructionResult(apply_patches(graph, local_patches(graph)), "roomplan")
    local = apply_patches(graph, local_patches(graph))
    return ReconstructionResult(apply_patches(local, remote, source="astra"), "astra")


def propose_patches(
    graph: SceneGraph,
    *,
    transport: Transport | None = None,
    frame_paths: Iterable[pathlib.Path] | None = None,
    poses_path: pathlib.Path | None = None,
    lidar_mesh_path: pathlib.Path | None = None,
) -> list[LabelPatch]:
    """Return remote patches only when the complete model response is valid."""
    return _remote_patches(
        graph, transport, frame_paths=frame_paths, poses_path=poses_path,
        lidar_mesh_path=lidar_mesh_path,
    ) or local_patches(graph)


def apply_patches(
    graph: SceneGraph, patches: list[LabelPatch], *, source: ReconstructionSource = "roomplan"
) -> SceneGraph:
    """A patch cannot resize, remove, invent, or unlock a node."""
    by_id = {patch.node_id: patch for patch in patches}
    return graph.model_copy(update={"nodes": [_apply_one(node, by_id.get(node.id), source) for node in graph.nodes]})


def local_patches(graph: SceneGraph) -> list[LabelPatch]:
    walls = [node for node in graph.nodes if node.kind == "wall"]
    objects = [node for node in graph.nodes if node.kind == "object"]
    return [_local_patch(node, walls, objects, graph) for node in graph.nodes]


def _apply_one(node: SceneNode, patch: LabelPatch | None, source: ReconstructionSource) -> SceneNode:
    if patch is None:
        return node
    if node.labeled_by == "owner" or node.quality == "confirmed":
        if source == "astra" and patch.reconstruction is not None:
            return node.model_copy(update={"reconstruction": patch.reconstruction})
        return node
    if source == "roomplan" and node.labeled_by == "astra":
        return node
    update: dict[str, Any] = {
        "label": patch.label or node.label,
        "movable": _locked_movable(node, patch.movable),
        "quality": node.quality if node.quality == "needs_another_look" else patch.quality,
    }
    if source == "astra":
        update["labeled_by"] = "astra"
        if patch.appearance is not None:
            update["appearance"] = patch.appearance
        if patch.reconstruction is not None:
            update["reconstruction"] = patch.reconstruction
    return node.model_copy(update=update)


def _locked_movable(node: SceneNode, proposed: bool) -> bool:
    if node.kind != "object" or node.raw_category in FIXED_CATEGORIES:
        return False
    if not node.movable:
        return False
    return proposed


def _local_patch(node: SceneNode, walls: list[SceneNode], objects: list[SceneNode], graph: SceneGraph) -> LabelPatch:
    label, movable = _local_identity(node, walls, graph)
    return LabelPatch(node.id, label, movable, _local_quality(node, objects))


def _local_identity(node: SceneNode, walls: list[SceneNode], graph: SceneGraph) -> tuple[str, bool]:
    if node.kind == "door":
        return _door_label(node, graph), False
    if node.kind != "object" or node.raw_category in FIXED_CATEGORIES:
        return node.label, False
    if node.label.strip().casefold() in COUNTER_LABELS:
        return node.label, False
    if _looks_like_counter(node, walls):
        return "Ordering counter", False
    return _named_furniture(node.raw_category) or (node.label, node.movable)


def _named_furniture(category: str) -> tuple[str, bool] | None:
    return {
        "storage": ("Display case", True), "chair": ("Chair", True), "table": ("Table", True),
        "sofa": ("Sofa", True), "stool": ("Stool", True), "bench": ("Bench", True),
    }.get(category)


def _door_label(node: SceneNode, graph: SceneGraph) -> str:
    doors = [item for item in graph.nodes if item.kind == "door"]
    return "Front door" if len(doors) == 1 and node.label.strip().casefold() == "door" else node.label


def _looks_like_counter(node: SceneNode, walls: list[SceneNode]) -> bool:
    return (
        node.raw_category in {"storage", "table", "counter"}
        and COUNTER_HEIGHT[0] <= node.dimensions.z <= COUNTER_HEIGHT[1]
        and max(node.dimensions.x, node.dimensions.y) >= COUNTER_LENGTH
        and any(gap_between(footprint(node), footprint(wall)) <= WALL_GAP_METERS for wall in walls)
    )


def _local_quality(node: SceneNode, objects: list[SceneNode]) -> QualityName:
    if node.quality == "confirmed" or node.kind != "object":
        return node.quality
    if min(node.dimensions.x, node.dimensions.y, node.dimensions.z) < THIN_METERS:
        return "needs_another_look"
    if any(gap_between(footprint(node), footprint(other)) < OVERLAP_METERS for other in objects if other.id != node.id):
        return "needs_another_look"
    return node.quality


def _remote_patches(
    graph: SceneGraph,
    transport: Transport | None,
    *,
    frame_paths: Iterable[pathlib.Path] | None = None,
    poses_path: pathlib.Path | None = None,
    lidar_mesh_path: pathlib.Path | None = None,
) -> list[LabelPatch] | None:
    api_key = os.environ.get(API_KEY_ENV)
    if transport is None and not api_key:
        return None
    objects = [node for node in graph.nodes if node.kind == "object"]
    batches = [objects[index:index + RECONSTRUCTION_BATCH_SIZE] for index in range(0, len(objects), RECONSTRUCTION_BATCH_SIZE)]
    if not batches:
        return None
    paths = tuple(frame_paths or ())
    deadline = time.monotonic() + RECONSTRUCTION_WALL_TIMEOUT_SECONDS
    executor: ThreadPoolExecutor | None = None
    try:
        mesh_profiles = object_mesh_profiles(graph, lidar_mesh_path)
        if time.monotonic() >= deadline:
            return None
        executor = ThreadPoolExecutor(max_workers=MAX_RECONSTRUCTION_WORKERS)
        futures = [executor.submit(
            _remote_batch, graph, batch, transport, api_key or "", paths, poses_path, mesh_profiles, deadline
        ) for batch in batches]
        patches: list[LabelPatch] = []
        for future in as_completed(futures, timeout=max(0.0, deadline - time.monotonic())):
            result = future.result()
            if result is None:
                logger.warning("astra_remote_batch_invalid reason=invalid_response")
                return None
            patches.extend(result)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError, ValueError) as error:
        logger.warning("astra_remote_batch_failed reason=%s", type(error).__name__)
        return None
    finally:
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
    return patches if {patch.node_id for patch in patches} == {node.id for node in objects} and len(patches) == len(objects) else None


def _remote_batch(
    graph: SceneGraph,
    objects: Sequence[SceneNode],
    transport: Transport | None,
    api_key: str,
    frame_paths: Iterable[pathlib.Path] | None,
    poses_path: pathlib.Path | None,
    mesh_profiles: dict[str, Any],
    deadline: float,
) -> list[LabelPatch] | None:
    if time.monotonic() >= deadline:
        return None
    scoped = graph.model_copy(update={"nodes": [node for node in graph.nodes if node.kind != "object"] + list(objects)})
    body = _chat_body(
        scoped,
        frame_paths=frame_paths,
        poses_path=poses_path,
        mesh_profiles=mesh_profiles,
    )
    if transport is not None:
        payload = transport(_chat_url(), body, _chat_headers(api_key))
    else:
        payload = _openrouter_post(_chat_url(), body, _chat_headers(api_key), deadline=deadline)
    return _patches_from_model(
        payload,
        scoped,
        allow_appearance=_body_has_images(body),
        evidence_by_node=_body_evidence_by_node(body),
    )


def _body_has_images(body: dict[str, Any]) -> bool:
    messages = body.get("messages") or []
    if len(messages) < 2 or not isinstance(messages[1], dict):
        return False
    content = messages[1].get("content")
    return isinstance(content, list) and any(
        isinstance(item, dict) and item.get("type") == "image_url" for item in content
    )


def _body_evidence_by_node(body: dict[str, Any]) -> dict[UUID, set[str]]:
    """Only calibrated crops establish photo evidence for display completion."""
    messages = body.get("messages") or []
    if len(messages) < 2 or not isinstance(messages[1], dict):
        return {}
    content = messages[1].get("content")
    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        return {}
    try:
        context = json.loads(content[0].get("text") or "")
    except (TypeError, ValueError):
        return {}
    evidence = context.get("photo_evidence") if isinstance(context, dict) else None
    result: dict[UUID, set[str]] = {}
    if not isinstance(evidence, list):
        return result
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get("frame_id"), str):
            continue
        frame_id = item["frame_id"]
        for raw_id in item.get("object_ids") or []:
            try:
                result.setdefault(UUID(str(raw_id)), set()).add(frame_id)
            except (TypeError, ValueError):
                continue
    return result


def _chat_url() -> str:
    return f"{(os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL).rstrip('/')}/chat/completions"


def _chat_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _chat_body(
    graph: SceneGraph,
    *,
    frame_paths: Iterable[pathlib.Path] | None = None,
    poses_path: pathlib.Path | None = None,
    lidar_mesh_path: pathlib.Path | None = None,
    mesh_profiles: dict[str, Any] | None = None,
) -> dict[str, Any]:
    calibrated = _calibrated_photo_evidence(graph, frame_paths, poses_path)
    image_messages = [item.message for item in calibrated] or _image_messages(graph, frame_paths, poses_path)
    content: str | list[dict[str, Any]] = json.dumps(
        _context(
            graph,
            images_provided=bool(image_messages),
            photo_evidence=_photo_evidence_context(calibrated),
            mesh_profiles=mesh_profiles if mesh_profiles is not None else object_mesh_profiles(graph, lidar_mesh_path),
        )
    )
    if image_messages:
        content = [{"type": "text", "text": content}, *image_messages]
    return {
        "model": os.environ.get(MODEL_ENV) or DEFAULT_MODEL,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "reasoning": {"effort": "low"},
        "messages": [{"role": "system", "content": INSTRUCTION}, {"role": "user", "content": content}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "astra_labels", "strict": True, "schema": LABEL_SCHEMA}},
        "provider": PROVIDER_ROUTING,
    }


@dataclass(frozen=True)
class _PoseEvidence:
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


@dataclass(frozen=True)
class _CalibratedEvidence:
    frame_id: str
    object_ids: tuple[UUID, ...]
    camera_local: tuple[float, float, float]
    message: dict[str, Any]


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
    paths = _unique_paths(frame_paths)
    if limit == 0 or not paths:
        return []
    poses = _load_poses(poses_path)
    if not poses:
        return _spread_paths(paths, limit)

    pose_by_key = {pose.frame_key: pose for pose in poses}
    candidates = [
        _FrameCandidate(path, score, visible, order)
        for order, path in enumerate(paths)
        for score, visible in [_best_frame_score(path, graph, pose_by_key)]
    ]
    objects = [node for node in graph.nodes if node.kind == "object"]
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


def _unique_paths(frame_paths: Iterable[pathlib.Path]) -> list[pathlib.Path]:
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
    pose_by_key: dict[str, _PoseEvidence],
    capture_to_room: Any = None,
) -> list[_FrameCandidate]:
    ranked: list[_FrameCandidate] = []
    for order, path in enumerate(paths):
        pose = pose_by_key.get(_frame_key(path.name))
        if pose is None:
            ranked.append(_FrameCandidate(path, -100.0, False, order))
            continue
        score, visible = _project_score(node, pose, capture_to_room)
        ranked.append(_FrameCandidate(path, score, visible, order))
    visible = [candidate for candidate in ranked if candidate.in_view]
    pool = visible or ranked
    return sorted(pool, key=lambda item: (-item.score, item.order))


def _best_frame_score(
    path: pathlib.Path,
    graph: SceneGraph,
    pose_by_key: dict[str, _PoseEvidence],
) -> tuple[float, bool]:
    pose = pose_by_key.get(_frame_key(path.name))
    if pose is None:
        return -100.0, False
    scores = [_project_score(node, pose, graph.capture_to_room) for node in graph.nodes if node.kind == "object"]
    if not scores:
        return 0.0, False
    return max(scores, key=lambda item: item[0])


def _frame_key(value: str) -> str:
    match = _FRAME_NUMBER.search(pathlib.Path(value).name)
    if match:
        return f"frame-{int(match.group(1))}"
    return pathlib.Path(value).stem.casefold()


def _load_poses(poses_path: pathlib.Path | None) -> list[_PoseEvidence]:
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
    poses: list[_PoseEvidence] = []
    for order, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("image"), str):
            continue
        transform = _numbers(item.get("transform"), 16)
        intrinsics = _numbers(item.get("intrinsics"), 9)
        if transform is None:
            continue
        poses.append(_PoseEvidence(
            frame_key=_frame_key(item["image"]),
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


def _project_score(
    node: SceneNode, pose: _PoseEvidence, capture_to_room: Any = None
) -> tuple[float, bool]:
    if pose.record is not None and pose.record.projectable and capture_to_room is not None:
        try:
            camera = camera_from_pose(pose.record, capture_to_room)
            point = node.transform.position
            pixel_x, pixel_y, depth = camera.project(np.array([[point.x, point.y, point.z]]))
            if depth[0] <= 0.05:
                return -float(abs(depth[0])), False
            margin_x, margin_y = camera.width * 0.25, camera.height * 0.25
            in_view = -margin_x <= pixel_x[0] <= camera.width + margin_x and -margin_y <= pixel_y[0] <= camera.height + margin_y
            offset = abs(pixel_x[0] - camera.cx) / max(camera.width / 2, 1) + abs(pixel_y[0] - camera.cy) / max(camera.height / 2, 1)
            return (10.0 if in_view else -4.0) - offset - float(depth[0]) * 0.02, in_view
        except (CameraMetadataError, ValueError, np.linalg.LinAlgError):
            pass
    matrix = pose.transform
    if len(matrix) != 16 or not all(math.isfinite(value) for value in matrix):
        return -100.0, False
    position = node.transform.position
    # Pose metadata stays in ARKit's Y-up, column-major coordinates.  Convert
    # the measured point back to that frame and use the camera basis directly;
    # conjugating the matrix would rotate the camera's local axes a second
    # time.  ARKit cameras look down local -Z.
    world = _room_to_capture(position.x, position.y, position.z, capture_to_room)
    camera = (matrix[12], matrix[13], matrix[14])
    delta = (world[0] - camera[0], world[1] - camera[1], world[2] - camera[2])
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
        return tuple((result[:3] / result[3]).tolist())  # type: ignore[return-value]
    except (TypeError, ValueError, np.linalg.LinAlgError):
        return x, z, -y


Candidate = tuple[float, str, pathlib.Path, tuple[int, int, int, int], str, tuple[float, ...], tuple[float, ...]]
Selection = tuple[UUID, str, pathlib.Path, tuple[int, int, int, int], str, tuple[float, float, float]]


def _crop_candidates(graph: SceneGraph, paths: list[pathlib.Path], poses_path) -> dict[UUID, list[Candidate]]:
    """Every object crop a calibrated pose can prove, keyed by object."""
    path_by_id = {_frame_key(path.name): path for path in paths}
    candidates: dict[UUID, list[Candidate]] = {}
    dimensions_match: dict[pathlib.Path, bool] = {}
    objects = [node for node in graph.nodes if node.kind == "object"]
    for pose in _load_poses(poses_path):
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
        crop, score = _projected_object_crop(node, camera)
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
    for node in (item for item in graph.nodes if item.kind == "object"):
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


def _encoded_evidence(selected: list[Selection]) -> list[_CalibratedEvidence]:
    evidence: list[_CalibratedEvidence] = []
    for node_id, frame_id, path, crop, orientation, camera_local in selected:
        encoded = _encode_crop(path, crop, orientation)
        if encoded is None:
            continue
        evidence.append(_CalibratedEvidence(
            frame_id=frame_id,
            object_ids=(node_id,),
            camera_local=camera_local,
            message={
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")},
            },
        ))
    return evidence


def _calibrated_photo_evidence(
    graph: SceneGraph,
    frame_paths: Iterable[pathlib.Path] | None,
    poses_path: pathlib.Path | None,
) -> list[_CalibratedEvidence]:
    """Create bounded object crops only when calibrated metadata proves the association.

    A full frame can help labels, but it does not establish that a particular
    measured object was observed.  Completion evidence therefore comes only
    from a camera projection through the graph's capture-to-room transform.
    """
    if graph.capture_to_room is None or not frame_paths:
        return []
    candidates = _crop_candidates(graph, _unique_paths(frame_paths), poses_path)
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
        return tuple(float(round(value, 3)) for value in bounded)
    except (TypeError, ValueError, np.linalg.LinAlgError):
        return (0.0, 0.0, 0.0)


def _projected_object_crop(node: SceneNode, camera: Any) -> tuple[tuple[int, int, int, int] | None, float]:
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


def _encode_crop(path: pathlib.Path, crop: tuple[int, int, int, int], orientation: str) -> bytes | None:
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


def _photo_evidence_context(evidence: Sequence[_CalibratedEvidence]) -> list[dict[str, Any]]:
    return [
        {
            "frame_id": item.frame_id,
            "object_ids": [str(node_id) for node_id in item.object_ids],
            "camera_local": list(item.camera_local),
        }
        for item in evidence
    ]


def _image_messages(
    graph: SceneGraph,
    frame_paths: Iterable[pathlib.Path] | None,
    poses_path: pathlib.Path | None,
) -> list[dict[str, Any]]:
    if not frame_paths:
        return []
    paths = select_keyframes(graph, frame_paths, poses_path)
    pose_by_key = {pose.frame_key: pose for pose in _load_poses(poses_path)}
    messages: list[dict[str, Any]] = []
    total_bytes = 0
    for path in paths:
        pose = pose_by_key.get(_frame_key(path.name))
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


def _context(
    graph: SceneGraph,
    *,
    images_provided: bool = False,
    photo_evidence: list[dict[str, Any]] | None = None,
    mesh_profiles: dict[UUID, Any] | None = None,
) -> dict[str, Any]:
    return {
        "walls": [_surface_brief(node) for node in graph.nodes if node.kind == "wall"],
        "objects": [
            _object_brief(node, (mesh_profiles or {}).get(str(node.id)))
            for node in graph.nodes if node.kind == "object"
        ],
        "doors": [_surface_brief(node) for node in graph.nodes if node.kind == "door"],
        "images_provided": images_provided,
        "photo_evidence": photo_evidence or [],
    }


def _surface_brief(node: SceneNode) -> dict[str, Any]:
    position = node.transform.position
    return {"id": str(node.id), "kind": node.kind, "label": node.label, "x": round(position.x, 3), "y": round(position.y, 3), "length": round(max(node.dimensions.x, node.dimensions.y), 3)}


def _object_brief(node: SceneNode, mesh_profile: Any = None) -> dict[str, Any]:
    position = node.transform.position
    brief = {
        "id": str(node.id), "raw_category": node.raw_category, "label": node.label,
        "width": round(node.dimensions.x, 3), "depth": round(node.dimensions.y, 3), "height": round(node.dimensions.z, 3),
        "x": round(position.x, 3), "y": round(position.y, 3), "movable": node.movable,
    }
    if mesh_profile is not None:
        brief["mesh_profile"] = mesh_profile
    return brief


def _openrouter_post(url: str, body: dict, headers: dict[str, str], *, deadline: float | None = None) -> dict:
    deadline = min(deadline or float("inf"), time.monotonic() + REQUEST_TIMEOUT_SECONDS)
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("openrouter_request_deadline")
    with urllib.request.urlopen(request, timeout=remaining) as response:
        raw = _read_response(response, deadline)
    if time.monotonic() > deadline:
        raise TimeoutError("openrouter_request_deadline")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("openrouter_not_an_object")
    return parsed


def _read_response(response: Any, deadline: float) -> bytes:
    """Read a response under one deadline, including slow heartbeat chunks."""
    chunks: list[bytes] = []
    total = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("openrouter_response_deadline")
        _set_response_timeout(response, remaining)
        chunk = response.read1(min(64 * 1024, MAX_RESPONSE_BYTES - total + 1))
        if not chunk:
            break
        if not isinstance(chunk, (bytes, bytearray)):
            raise ValueError("openrouter_response_not_bytes")
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ValueError("openrouter_response_too_large")
        chunks.append(bytes(chunk))
    return b"".join(chunks)


def _set_response_timeout(response: Any, seconds: float) -> None:
    stream = getattr(response, "fp", None)
    raw = getattr(stream, "raw", None)
    socket = getattr(raw, "_sock", None) or getattr(stream, "_sock", None)
    setter = getattr(socket, "settimeout", None)
    if callable(setter):
        setter(max(0.001, seconds))


def _patches_from_model(
    payload: dict,
    graph: SceneGraph,
    *,
    allow_appearance: bool = True,
    evidence_by_node: dict[UUID, set[str]] | None = None,
) -> list[LabelPatch] | None:
    content = _message_content(payload)
    if content is None:
        return None
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict) or set(parsed) != {"nodes"}:
        return None
    items = parsed.get("nodes")
    if not isinstance(items, list):
        return None
    expected = {node.id for node in graph.nodes if node.kind == "object"}
    if not expected:
        return None
    patches = [
        _patch_from_item(
            item,
            expected,
            allow_appearance=allow_appearance,
            evidence_frame_ids=(evidence_by_node or {}),
        )
        for item in items
    ]
    if any(patch is None for patch in patches):
        return None
    found = [patch for patch in patches if patch is not None]
    return found if {patch.node_id for patch in found} == expected and len(found) == len(expected) else None


def _message_content(payload: dict) -> str | None:
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message") or {}
    content = message.get("content")
    return content if isinstance(content, str) else None


ITEM_KEYS = {"id", "label", "movable", "quality", "appearance", "reconstruction"}
QUALITIES = {"measured", "needs_another_look"}


def _node_id_of(item: dict, expected: set[UUID]) -> UUID | None:
    try:
        node_id = UUID(str(item.get("id")))
    except (ValueError, TypeError):
        return None
    return node_id if node_id in expected else None


def _labelling_of(item: dict) -> tuple[str, str, bool] | None:
    """The three fields every patch must carry, or None if any is unusable."""
    label, quality, movable = item.get("label"), item.get("quality"), item.get("movable")
    if not isinstance(label, str) or not label.strip():
        return None
    if quality not in QUALITIES or not isinstance(movable, bool):
        return None
    return label, quality, movable


def _reconstruction_of(raw: object, allowed: set[str]) -> DisplayReconstruction | None:
    """A reconstruction is kept only when every frame it cites is one we gave it.

    A model that cites a frame it was never shown, or cites one twice, has
    invented its evidence, so the reconstruction is dropped rather than trusted.
    """
    try:
        reconstruction = DisplayReconstruction.model_validate(raw)
    except (TypeError, ValueError):
        return None
    cited = reconstruction.evidence_frame_ids
    if not allowed or len(set(cited)) != len(cited) or not set(cited).issubset(allowed):
        return None
    return reconstruction


def _patch_from_item(
    item: object,
    expected: set[UUID],
    *,
    allow_appearance: bool = True,
    evidence_frame_ids: dict[UUID, set[str]] | None = None,
) -> LabelPatch | None:
    if not isinstance(item, dict) or set(item) - ITEM_KEYS:
        return None
    node_id = _node_id_of(item, expected)
    labelling = _labelling_of(item) if node_id is not None else None
    if node_id is None or labelling is None:
        return None
    label, quality, movable = labelling

    appearance = None
    raw_appearance = item.get("appearance")
    if raw_appearance is not None:
        if not allow_appearance:
            return None
        try:
            appearance = DisplayAppearance.model_validate(raw_appearance)
        except (TypeError, ValueError):
            return None

    raw_reconstruction = item.get("reconstruction")
    reconstruction = None
    if raw_reconstruction is not None:
        reconstruction = _reconstruction_of(raw_reconstruction, (evidence_frame_ids or {}).get(node_id, set()))

    return LabelPatch(node_id, label.strip()[:80], movable, quality, appearance, reconstruction)
