"""Secondary semantic corrections for Standard Physics (Phase G).

Reuses the evidence/attachment pattern for whiteboards and photo-supported sofa/table
label corrections.

Core Invariants:
1. Preserve raw_category, measured geometry, provenance, and owner corrections.
2. Do not infer sofa identity just from length; require photographic evidence.
3. Do not fabricate a missing whiteboard; require confident photographic detection.
4. An identity correction must not silently change dimensions or collision.
5. Protect owner edits against later automated overwrites.
"""

from __future__ import annotations

import logging
import uuid
from typing import Sequence

import numpy as np
from standardphysics_contracts import (
    Mat4,
    ObservationCrop,
    SceneGraph,
    SceneNode,
    SurfaceAttachment,
    Vec3,
    bounds_the_room,
)

from ..occupancy import reads_as_wall
from ..textures.camera import PhotoCamera
from .detect import Detection
from .surface_attach import intersect_node_surface, ray_for_pixel

log = logging.getLogger(__name__)

WHITEBOARD_NAMES = frozenset({
    "whiteboard",
    "chalkboard",
    "dry_erase_board",
    "writing_board",
    "blackboard",
    "presentation_board",
})

SOFA_NAMES = frozenset({
    "sofa",
    "couch",
    "loveseat",
    "sectional",
    "armchair",
    "lounge_chair",
})

TABLE_NAMES = frozenset({
    "table",
    "desk",
    "dining_table",
    "coffee_table",
    "side_table",
    "conference_table",
})

MIN_FURNITURE_CORRECTION_CONFIDENCE = 0.75
MIN_WHITEBOARD_CONFIDENCE = 0.70

SEMANTIC_CORRECTION_NAMESPACE = uuid.UUID("a9e5b3c1-7d2f-4e8a-9b1c-3f5e7a9b0c2d")


def _is_owner_protected(node: SceneNode) -> bool:
    """Owner corrections and confirmed review statuses must never be overwritten."""
    if node.labeled_by == "owner":
        return True
    if node.attachment is not None and node.attachment.review_status in ("confirmed_by_user", "rejected_by_user"):
        return True
    return False


def _project_point(camera: PhotoCamera, point_room: np.ndarray) -> tuple[float, float, float] | None:
    """Projects a 3D point in room frame to camera pixel coordinates (col, row, depth)."""
    p_hom = np.append(point_room, 1.0)
    p_cam = camera.room_to_camera @ p_hom
    depth = float(p_cam[2])
    if depth <= 0.1:
        return None
    col = float(camera.fx * p_cam[0] / depth + camera.cx)
    row = float(camera.fy * p_cam[1] / depth + camera.cy)
    return col, row, depth


def _box_contains_point(box_2d: Sequence[float], col: float, row: float, margin_fraction: float = 0.10) -> bool:
    """Whether (col, row) is within the detection bounding box [left, top, right, bottom]."""
    left, top, right, bottom = box_2d
    w = right - left
    h = bottom - top
    return (
        (left - margin_fraction * w) <= col <= (right + margin_fraction * w)
        and (top - margin_fraction * h) <= row <= (bottom + margin_fraction * h)
    )


def _detection_name(detection: Detection) -> str:
    return getattr(detection, "name", getattr(detection, "label", ""))


def _detection_box(detection: Detection) -> tuple[float, float, float, float]:
    box = getattr(detection, "box", getattr(detection, "box_2d", (0.0, 0.0, 0.0, 0.0)))
    return tuple(box)  # type: ignore


def correct_furniture_label(
    node: SceneNode,
    detection: Detection,
    camera: PhotoCamera,
) -> SceneNode | None:
    """Photo-supported sofa/table label correction.

    Preserves raw_category, measured dimensions, and transform.
    Rejects length-only heuristics: requires genuine photographic detection evidence.
    """
    if bounds_the_room(node):
        return None

    if _is_owner_protected(node):
        log.debug("Node %s is owner-protected; skipping automated relabeling", node.id)
        return None

    if detection.confidence < MIN_FURNITURE_CORRECTION_CONFIDENCE:
        return None

    name = _detection_name(detection)
    label_clean = name.strip().lower().replace(" ", "_")
    target_label: str | None = None
    if label_clean in SOFA_NAMES:
        target_label = "Sofa"
    elif label_clean in TABLE_NAMES:
        target_label = "Table"

    if target_label is None:
        return None

    # Do not re-label if it already has this label
    if node.label.strip().capitalize() == target_label:
        return None

    # Project node center into camera frame to verify visibility and overlap
    pos = node.transform.position
    node_center = np.array([pos.x, pos.y, pos.z], dtype=np.float64)
    proj = _project_point(camera, node_center)
    if proj is None:
        return None

    col, row, _depth = proj
    box = _detection_box(detection)
    if not _box_contains_point(box, col, row):
        return None

    # Apply correction: preserve raw_category, dimensions, and transform exactly
    return node.model_copy(update={
        "label": target_label,
        "labeled_by": "discovery",
    })


def detect_and_attach_whiteboard(
    wall_node: SceneNode,
    detection: Detection,
    camera: PhotoCamera,
) -> SceneNode | None:
    """Identifies a whiteboard from photo evidence and attaches it to a wall.

    Does not fabricate missing whiteboards: requires confident photographic evidence.
    Does not alter wall collision: surface attachment metadata excludes it from solid obstacles.
    """
    if not reads_as_wall(wall_node):
        return None

    if detection.confidence < MIN_WHITEBOARD_CONFIDENCE:
        return None

    name = _detection_name(detection)
    label_clean = name.strip().lower().replace(" ", "_")
    if label_clean not in WHITEBOARD_NAMES:
        return None

    # Center pixel of detection box
    box = _detection_box(detection)
    left, top, right, bottom = box
    center_col = (left + right) / 2.0
    center_row = (top + bottom) / 2.0

    origin, direction = ray_for_pixel(camera, center_col, center_row)
    hit = intersect_node_surface(origin, direction, wall_node, margin=0.15)
    if hit is None:
        return None

    _dist, hit_point, normal = hit

    # Estimate dimensions based on detection box span at hit distance
    cam_z = float((camera.room_to_camera @ np.append(hit_point, 1.0))[2])
    width_m = max(0.40, min(3.0, float((right - left) * cam_z / camera.fx)))
    height_m = max(0.30, min(2.0, float((bottom - top) * cam_z / camera.fy)))

    # Orientation aligned with wall surface normal
    z_axis = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    x_axis = np.cross(normal, z_axis)
    x_norm = np.linalg.norm(x_axis)
    if x_norm > 1e-6:
        x_axis /= x_norm
    else:
        x_axis = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    y_axis = normal

    rot_matrix = np.eye(4, dtype=np.float64)
    rot_matrix[:3, 0] = x_axis
    rot_matrix[:3, 1] = y_axis
    rot_matrix[:3, 2] = z_axis
    rot_matrix[:3, 3] = hit_point

    whiteboard_id = uuid.uuid5(
        SEMANTIC_CORRECTION_NAMESPACE,
        f"{wall_node.id}|whiteboard|{hit_point[0]:.2f}_{hit_point[1]:.2f}_{hit_point[2]:.2f}",
    )

    crop = ObservationCrop(
        frame_id=camera.frame_id,
        sensor_box=[float(x) for x in box],
        confidence=float(detection.confidence),
        image_url=None,
    )

    attachment = SurfaceAttachment(
        support_node_id=wall_node.id,
        support_type="lidar_surface",
        normal=Vec3(x=float(normal[0]), y=float(normal[1]), z=float(normal[2])),
        localization_quality="verified_support",
        identity_confidence=float(detection.confidence),
        review_status="detected",
        observations=[crop],
        uncertainty_reasons=[
            "extent is an estimate from the wall-plane span of the observed region, "
            "not a physical device size; never read as a measured whiteboard"
        ],
    )

    return SceneNode(
        id=whiteboard_id,
        kind="whiteboard",
        label="Whiteboard",
        raw_category="whiteboard",
        dimensions=Vec3(x=round(width_m, 3), y=0.02, z=round(height_m, 3)),
        transform=Mat4(m=[round(val, 6) for val in rot_matrix.ravel().tolist()]),
        parent_id=wall_node.id,
        relation="attached_to",
        quality="needs_another_look",
        attachment=attachment,
    )


def _best_whiteboard(
    wall_node: SceneNode,
    detections_by_frame: dict[str, list[Detection]],
    cameras_by_id: dict[str, PhotoCamera],
) -> SceneNode | None:
    """The highest-confidence whiteboard detection on one wall, or nothing."""
    best_board: SceneNode | None = None
    best_confidence = -1.0
    for frame_id, detections in detections_by_frame.items():
        camera = cameras_by_id.get(frame_id)
        if camera is None:
            continue
        for det in detections:
            board = detect_and_attach_whiteboard(wall_node, det, camera)
            if board is not None and det.confidence > best_confidence:
                best_board = board
                best_confidence = det.confidence
    return best_board


def apply_secondary_semantic_corrections(
    graph: SceneGraph,
    detections_by_frame: dict[str, list[Detection]],
    cameras: list[PhotoCamera],
) -> SceneGraph:
    """Applies photo-supported semantic corrections to the scene graph.

    - Relabels furniture when photographic evidence contradicts original RoomPlan label.
    - Adds wall-attached whiteboards evidenced by photos.
    - Preserves owner corrections, raw categories, and measured geometry.
    """
    cameras_by_id = {cam.frame_id: cam for cam in cameras}
    updated_nodes: list[SceneNode] = []
    whiteboards: list[SceneNode] = []

    # 1. Check existing nodes for sofa/table corrections
    for node in graph.nodes:
        current_node = node
        # Try to find a photo detection that provides a label correction
        for frame_id, detections in detections_by_frame.items():
            camera = cameras_by_id.get(frame_id)
            if camera is None:
                continue
            for det in detections:
                corrected = correct_furniture_label(current_node, det, camera)
                if corrected is not None:
                    current_node = corrected
                    break
            if current_node.labeled_by == "discovery":
                break
        updated_nodes.append(current_node)

    # 2. Check walls for whiteboard attachments: one board per wall,
    # the best-evidenced view, so many frames of one board are not many boards.
    whiteboards = [
        board
        for wall in updated_nodes
        if reads_as_wall(wall)
        for board in [_best_whiteboard(wall, detections_by_frame, cameras_by_id)]
        if board is not None
    ]

    all_nodes = [*updated_nodes, *whiteboards]
    return graph.model_copy(update={
        "revision": graph.revision + (1 if (len(whiteboards) > 0 or any(n.labeled_by == "discovery" for n in updated_nodes)) else 0),
        "nodes": all_nodes,
    })
