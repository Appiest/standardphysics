"""Attach photo-supported objects to measured support surfaces.

A vision detection provides a 2D box in a photograph. This projects calibrated
rays from the camera through the observed region, intersects measured LiDAR
support surfaces or RoomPlan planes, checks for occlusions, grazing angles, and
normals, and produces an authoritative SurfaceAttachment.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
from standardphysics_contracts import (
    Mat4,
    ObservationCrop,
    SceneGraph,
    SceneNode,
    SocketTarget,
    SurfaceAttachment,
    Vec3,
    bounds_the_room,
)

from ..textures.camera import PhotoCamera
from . import taxonomy
from .boxes import _frame, to_local
from .detect import Detection

GRAZING_ANGLE_COS_THRESHOLD = 0.2588  # ~75 degrees from normal (cos 75 deg ≈ 0.2588)
MIN_SUPPORT_RAYS = 3
TOTAL_SAMPLE_RAYS = 9
FACEPLATE_DEFAULT_SIZE = (0.12, 0.03, 0.12)  # Width 12cm, thickness 3cm, height 12cm


def _review_status(detection: Detection) -> str:
    """A counter or restroom entrance stays a candidate until the owner confirms its role and public use."""
    return "candidate" if detection.needs_owner_confirmation else detection.review_status


def _node_kind(detection: Detection) -> str:
    if detection.is_outlet:
        return "outlet"
    if detection.is_confuser:
        return "confuser"
    return detection.class_key


def _node_label(detection: Detection) -> str:
    if detection.is_outlet:
        return f"Outlet ({detection.name})"
    if detection.is_confuser or detection.class_key == taxonomy.OBJECT:
        return detection.name
    return f"{taxonomy.semantic_class(detection.class_key).label} ({detection.name})"


def _candidate_kind(detection: Detection) -> str:
    if detection.is_outlet:
        return "candidate_outlet"
    return f"candidate_{detection.class_key}"


@dataclass(frozen=True)
class RayHit:
    col: float
    row: float
    t: float
    point: np.ndarray
    normal: np.ndarray
    support_node: SceneNode
    is_grazing: bool
    is_occluded: bool


def ray_for_pixel(camera: PhotoCamera, col: float, row: float) -> tuple[np.ndarray, np.ndarray]:
    """Origin and normalized direction in the room frame for one pixel."""
    origin = camera.position
    rotation = camera.room_to_camera[:3, :3]
    # Direction in camera frame (+X right, +Y down, +Z forward)
    cam_dir = np.array([(col - camera.cx) / camera.fx, (row - camera.cy) / camera.fy, 1.0], dtype=np.float64)
    room_dir = rotation.T @ cam_dir
    norm = np.linalg.norm(room_dir)
    if norm > 1e-9:
        room_dir /= norm
    return origin, room_dir


def intersect_node_surface(
    origin: np.ndarray,
    direction: np.ndarray,
    node: SceneNode,
    margin: float = 0.05,
) -> tuple[float, np.ndarray, np.ndarray] | None:
    """Intersects a ray with the nearest visible face of a SceneNode.
    
    Returns (distance, hit_point, outward_normal) in room frame, or None if no hit.
    """
    rot, node_origin, half = _frame(node)
    # Broadest face normal for walls: usually local axis with smallest extent is the thickness
    extents = node.dimensions.as_tuple()
    thickness_axis = int(np.argmin(extents))
    normal_candidate = rot[:, thickness_axis]

    # Orient normal towards the camera origin
    if np.dot(normal_candidate, origin - node_origin) < 0:
        normal = -normal_candidate
    else:
        normal = normal_candidate

    denom = np.dot(direction, normal)
    # Reject if ray is nearly parallel to surface or looking away from camera
    if denom >= -1e-4:
        return None

    # Plane passes through the surface face (offset by half thickness along normal)
    face_origin = node_origin + normal * half[thickness_axis]
    t = float(np.dot(face_origin - origin, normal) / denom)
    if t <= 0.05:  # Behind camera or too close
        return None

    hit_point = origin + t * direction
    local_hit = np.abs(to_local(hit_point, node))
    # Check within bounds
    if np.all(local_hit <= half + margin):
        return t, hit_point, normal

    return None


def sample_ray_points(box: tuple[float, float, float, float]) -> list[tuple[float, float]]:
    """Samples 9 representative points across the 2D bounding box (col, row)."""
    left, top, right, bottom = box
    w = right - left
    h = bottom - top
    fractions = [
        (0.5, 0.5),  # center
        (0.15, 0.15),  # top-left
        (0.85, 0.15),  # top-right
        (0.15, 0.85),  # bottom-left
        (0.85, 0.85),  # bottom-right
        (0.5, 0.15),  # top-mid
        (0.5, 0.85),  # bottom-mid
        (0.15, 0.5),  # left-mid
        (0.85, 0.5),  # right-mid
    ]
    return [(left + fx * w, top + fy * h) for fx, fy in fractions]


def cast_and_intersect(
    camera: PhotoCamera,
    points: list[tuple[float, float]],
    candidate_nodes: list[SceneNode],
    depth_buffer: np.ndarray | None = None,
) -> list[RayHit]:
    """Casts rays through sample points and tests intersections against candidate nodes."""
    hits: list[RayHit] = []
    for col, row in points:
        origin, direction = ray_for_pixel(camera, col, row)
        best_hit: tuple[float, np.ndarray, np.ndarray, SceneNode] | None = None
        for node in candidate_nodes:
            res = intersect_node_surface(origin, direction, node)
            if res is not None:
                t, hit_pt, normal = res
                if best_hit is None or t < best_hit[0]:
                    best_hit = (t, hit_pt, normal, node)
        if best_hit is not None:
            t, hit_pt, normal, node = best_hit
            cos_angle = abs(float(np.dot(direction, normal)))
            is_grazing = cos_angle < GRAZING_ANGLE_COS_THRESHOLD
            is_occluded = False
            if depth_buffer is not None:
                h_buf, w_buf = depth_buffer.shape
                c_idx = int(np.clip(round(col * w_buf / camera.width), 0, w_buf - 1))
                r_idx = int(np.clip(round(row * h_buf / camera.height), 0, h_buf - 1))
                buf_depth = float(depth_buffer[r_idx, c_idx])
                hit_cam_z = float(camera.to_camera(hit_pt.reshape(1, 3))[0, 2])
                if math.isfinite(buf_depth) and buf_depth < hit_cam_z - 0.08:
                    is_occluded = True
            hits.append(RayHit(col, row, t, hit_pt, normal, node, is_grazing, is_occluded))
    return hits


def attach_detection_to_surface(
    detection: Detection,
    camera: PhotoCamera,
    graph: SceneGraph,
    *,
    depth_buffer: np.ndarray | None = None,
    image_url: str | None = None,
    expected_revision: int | None = None,
) -> tuple[SurfaceAttachment, SceneNode]:
    """Projects a 2D outlet detection onto the scene's support surfaces and creates a SceneNode."""
    if expected_revision is not None and graph.revision != expected_revision:
        raise ValueError(f"stale scene graph revision: expected {expected_revision}, got {graph.revision}")

    candidate_nodes = [n for n in graph.nodes if bounds_the_room(n) or n.kind in ("wall", "counter", "table", "desk")]
    sample_points = sample_ray_points(detection.box)
    all_hits = cast_and_intersect(camera, sample_points, candidate_nodes, depth_buffer)

    unoccluded_hits = [h for h in all_hits if not h.is_occluded]
    uncertainty_reasons: list[str] = []

    if len(all_hits) > 0 and len(unoccluded_hits) == 0:
        uncertainty_reasons.append("detection is fully occluded by foreground geometry")

    # Find dominant support node
    support_counts: dict[uuid.UUID, int] = {}
    for h in unoccluded_hits:
        support_counts[h.support_node.id] = support_counts.get(h.support_node.id, 0) + 1

    dominant_node_id = max(support_counts, key=support_counts.get) if support_counts else None
    dominant_hits = [h for h in unoccluded_hits if h.support_node.id == dominant_node_id] if dominant_node_id else []

    obs_crop = ObservationCrop(
        frame_id=detection.frame_id,
        sensor_box=list(detection.box),
        confidence=detection.confidence,
        image_url=image_url,
    )

    if len(dominant_hits) >= MIN_SUPPORT_RAYS and dominant_node_id is not None:
        support_node = graph.by_id(dominant_node_id)
        hit_points = np.stack([h.point for h in dominant_hits], axis=0)
        center_pt = np.mean(hit_points, axis=0)
        normal_vec = dominant_hits[0].normal

        if len(support_counts) > 1:
            uncertainty_reasons.append("mixed support surfaces intersected near object boundary")
        elif len(dominant_hits) < len(sample_points):
            uncertainty_reasons.append("partial support coverage near surface edge or mesh boundary")

        if any(h.is_grazing for h in dominant_hits):
            uncertainty_reasons.append("camera ray grazing angle exceeds 75 degrees from surface normal")

        has_lidar_verification = False
        if depth_buffer is not None:
            h_buf, w_buf = depth_buffer.shape
            matching_hits = 0
            for h in dominant_hits:
                c_idx = int(np.clip(round(h.col * w_buf / camera.width), 0, w_buf - 1))
                r_idx = int(np.clip(round(h.row * h_buf / camera.height), 0, h_buf - 1))
                buf_d = float(depth_buffer[r_idx, c_idx])
                hit_z = float(camera.to_camera(h.point.reshape(1, 3))[0, 2])
                if math.isfinite(buf_d) and abs(buf_d - hit_z) <= 0.08:
                    matching_hits += 1
            if matching_hits >= MIN_SUPPORT_RAYS:
                has_lidar_verification = True

        support_type = "lidar_surface" if has_lidar_verification else "roomplan_plane"
        if support_type == "roomplan_plane":
            uncertainty_reasons.append("attached to inferred RoomPlan plane; not independently verified by LiDAR")

        local_anchor = to_local(center_pt, support_node)

        # Separate socket targets
        sockets: list[SocketTarget] = []
        for i, (s_col, s_row) in enumerate(detection.sockets):
            s_origin, s_dir = ray_for_pixel(camera, s_col, s_row)
            s_res = intersect_node_surface(s_origin, s_dir, support_node)
            if s_res is not None:
                _, s_pt, _ = s_res
                sockets.append(SocketTarget(
                    id=f"{detection.frame_id}_sock_{i}",
                    center=Vec3(x=float(s_pt[0]), y=float(s_pt[1]), z=float(s_pt[2])),
                    status="observed",
                    confidence=detection.confidence,
                ))

        # Observed region corners in 3D
        corner_points = [
            (detection.box[0], detection.box[1]),
            (detection.box[2], detection.box[1]),
            (detection.box[2], detection.box[3]),
            (detection.box[0], detection.box[3]),
        ]
        observed_region: list[Vec3] = []
        for c_col, c_row in corner_points:
            c_orig, c_dir = ray_for_pixel(camera, c_col, c_row)
            c_res = intersect_node_surface(c_orig, c_dir, support_node)
            if c_res is not None:
                _, c_pt, _ = c_res
                observed_region.append(Vec3(x=float(c_pt[0]), y=float(c_pt[1]), z=float(c_pt[2])))

        localization_quality = "verified_support" if support_type == "lidar_surface" and not uncertainty_reasons else "needs_verification"

        attachment = SurfaceAttachment(
            support_node_id=support_node.id,
            support_type=support_type,
            local_anchor=Vec3(x=float(local_anchor[0]), y=float(local_anchor[1]), z=float(local_anchor[2])),
            normal=Vec3(x=float(normal_vec[0]), y=float(normal_vec[1]), z=float(normal_vec[2])),
            observed_region=observed_region,
            sockets=sockets,
            observations=[obs_crop],
            identity_confidence=detection.confidence,
            localization_quality=localization_quality,
            review_status=_review_status(detection),
            uncertainty_reasons=uncertainty_reasons,
        )

        # Build transform aligned with surface normal and upright Z
        up = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        tangent = np.cross(normal_vec, up)
        if np.linalg.norm(tangent) < 1e-4:
            tangent = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        else:
            tangent /= np.linalg.norm(tangent)
        bitangent = np.cross(normal_vec, tangent)
        rot_matrix = np.column_stack([tangent, normal_vec, bitangent])

        stable_seed = f"{support_node.id}_{round(float(center_pt[0]), 2)}_{round(float(center_pt[1]), 2)}_{round(float(center_pt[2]), 2)}"
        node_id = uuid.uuid5(uuid.NAMESPACE_OID, stable_seed)

        node = SceneNode(
            id=node_id,
            kind=_node_kind(detection),
            label=_node_label(detection),
            raw_category=detection.name,
            dimensions=Vec3(x=FACEPLATE_DEFAULT_SIZE[0], y=FACEPLATE_DEFAULT_SIZE[1], z=FACEPLATE_DEFAULT_SIZE[2]),
            transform=Mat4(m=[
                rot_matrix[0, 0], rot_matrix[0, 1], rot_matrix[0, 2], float(center_pt[0]),
                rot_matrix[1, 0], rot_matrix[1, 1], rot_matrix[1, 2], float(center_pt[1]),
                rot_matrix[2, 0], rot_matrix[2, 1], rot_matrix[2, 2], float(center_pt[2]),
                0.0, 0.0, 0.0, 1.0,
            ]),
            quality="measured" if support_type == "lidar_surface" else "needs_another_look",
            movable=False,
            labeled_by="discovery",
            parent_id=support_node.id,
            relation="mounted_on",
            attachment=attachment,
        )
        return attachment, node

    # Fallback: unanchored candidate
    uncertainty_reasons.append("no support surface intersected by sufficient rays; unanchored candidate")
    attachment = SurfaceAttachment(
        support_node_id=None,
        support_type="unanchored",
        observations=[obs_crop],
        identity_confidence=detection.confidence,
        localization_quality="unanchored",
        review_status="candidate",
        uncertainty_reasons=uncertainty_reasons,
    )
    # Estimate dummy position in front of camera
    cam_pos = camera.position
    fwd = camera.forward
    est_pt = cam_pos + fwd * 1.5
    stable_seed = f"unanchored_{detection.frame_id}_{round(float(detection.box[0]), 1)}_{round(float(detection.box[1]), 1)}"
    node_id = uuid.uuid5(uuid.NAMESPACE_OID, stable_seed)
    node = SceneNode(
        id=node_id,
        kind=_candidate_kind(detection),
        label=f"Candidate {taxonomy.semantic_class(detection.class_key).label.lower()} ({detection.name})",
        raw_category=detection.name,
        dimensions=Vec3(x=FACEPLATE_DEFAULT_SIZE[0], y=FACEPLATE_DEFAULT_SIZE[1], z=FACEPLATE_DEFAULT_SIZE[2]),
        transform=Mat4(m=[
            1.0, 0.0, 0.0, float(est_pt[0]),
            0.0, 1.0, 0.0, float(est_pt[1]),
            0.0, 0.0, 1.0, float(est_pt[2]),
            0.0, 0.0, 0.0, 1.0,
        ]),
        quality="needs_another_look",
        movable=False,
        labeled_by="discovery",
        parent_id=None,
        relation=None,
        attachment=attachment,
    )
    return attachment, node
