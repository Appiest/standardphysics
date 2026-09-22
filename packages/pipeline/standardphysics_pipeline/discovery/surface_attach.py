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


def _surface_dimensions(
    detection: Detection,
    observed_region: list[Vec3],
    normal: np.ndarray,
) -> Vec3:
    """The node's extent, separated into what is and is not measured.

    An outlet faceplate is a documented default: the size of the plate is
    standard and small. Everything else attached to a surface has no device
    size from a photo: the honest extent is the span of the observed region
    on the support plane, with depth left as a display-only proxy and the
    node marked needs_another_look, never measured.
    """
    if detection.is_outlet:
        return Vec3(x=FACEPLATE_DEFAULT_SIZE[0], y=FACEPLATE_DEFAULT_SIZE[1], z=FACEPLATE_DEFAULT_SIZE[2])
    if not observed_region:
        return Vec3(x=0.0, y=0.0, z=0.0)
    up = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    tangent = np.cross(normal, up)
    if np.linalg.norm(tangent) < 1e-4:
        tangent = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    else:
        tangent /= np.linalg.norm(tangent)
    bitangent = np.cross(normal, tangent)
    points = np.asarray([[point.x, point.y, point.z] for point in observed_region], dtype=np.float64)
    span = points @ tangent
    rise = points @ bitangent
    return Vec3(
        x=round(float(span.max() - span.min()), 3),
        y=FACEPLATE_DEFAULT_SIZE[1],
        z=round(float(rise.max() - rise.min()), 3),
    )


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


def _dominant_support(
    unoccluded_hits: list[RayHit],
) -> tuple[dict[uuid.UUID, int], uuid.UUID | None, list[RayHit]]:
    support_counts: dict[uuid.UUID, int] = {}
    for hit in unoccluded_hits:
        support_counts[hit.support_node.id] = support_counts.get(hit.support_node.id, 0) + 1
    dominant_node_id = max(support_counts, key=support_counts.get) if support_counts else None
    dominant_hits = [h for h in unoccluded_hits if h.support_node.id == dominant_node_id] if dominant_node_id else []
    return support_counts, dominant_node_id, dominant_hits


def _lidar_verified(camera: PhotoCamera, dominant_hits: list[RayHit], depth_buffer: np.ndarray | None) -> bool:
    if depth_buffer is None:
        return False
    h_buf, w_buf = depth_buffer.shape
    matching_hits = 0
    for hit in dominant_hits:
        c_idx = int(np.clip(round(hit.col * w_buf / camera.width), 0, w_buf - 1))
        r_idx = int(np.clip(round(hit.row * h_buf / camera.height), 0, h_buf - 1))
        buffer_depth = float(depth_buffer[r_idx, c_idx])
        hit_z = float(camera.to_camera(hit.point.reshape(1, 3))[0, 2])
        if math.isfinite(buffer_depth) and abs(buffer_depth - hit_z) <= 0.08:
            matching_hits += 1
    return matching_hits >= MIN_SUPPORT_RAYS


def _socket_targets(detection: Detection, camera: PhotoCamera, support_node: SceneNode) -> list[SocketTarget]:
    sockets: list[SocketTarget] = []
    for index, (s_col, s_row) in enumerate(detection.sockets):
        origin, direction = ray_for_pixel(camera, s_col, s_row)
        hit = intersect_node_surface(origin, direction, support_node)
        if hit is None:
            continue
        _, point, _ = hit
        sockets.append(SocketTarget(
            id=f"{detection.frame_id}_sock_{index}",
            center=Vec3(x=float(point[0]), y=float(point[1]), z=float(point[2])),
            status="observed",
            confidence=detection.confidence,
        ))
    return sockets


def _observed_region(detection: Detection, camera: PhotoCamera, support_node: SceneNode) -> list[Vec3]:
    corners = [
        (detection.box[0], detection.box[1]),
        (detection.box[2], detection.box[1]),
        (detection.box[2], detection.box[3]),
        (detection.box[0], detection.box[3]),
    ]
    region: list[Vec3] = []
    for col, row in corners:
        origin, direction = ray_for_pixel(camera, col, row)
        hit = intersect_node_surface(origin, direction, support_node)
        if hit is None:
            continue
        _, point, _ = hit
        region.append(Vec3(x=float(point[0]), y=float(point[1]), z=float(point[2])))
    return region


def _node_size(
    detection: Detection,
    support_type: str,
    observed_region: list[Vec3],
    normal: np.ndarray,
    uncertainty_reasons: list[str],
) -> tuple[Vec3, str]:
    """The node's extent and its quality, never letting proxy geometry read as measured."""
    dimensions = _surface_dimensions(detection, observed_region, normal)
    if detection.is_outlet:
        return dimensions, "measured" if support_type == "lidar_surface" else "needs_another_look"
    if not any("display proxy geometry" in reason for reason in uncertainty_reasons):
        uncertainty_reasons.append(
            "display proxy geometry: extent is the wall-plane span of the observed region, "
            "not a verified device size; never read as a measured television"
        )
    return dimensions, "needs_another_look"


def _anchored_attachment(
    detection: Detection,
    camera: PhotoCamera,
    graph: SceneGraph,
    dominant_node_id: uuid.UUID,
    dominant_hits: list[RayHit],
    support_counts: dict[uuid.UUID, int],
    sample_point_count: int,
    uncertainty_reasons: list[str],
    obs_crop: ObservationCrop,
    depth_buffer: np.ndarray | None,
) -> tuple[SurfaceAttachment, SceneNode]:
    """Builds the verified or qualified attachment and its node on the dominant support."""
    support_node = graph.by_id(dominant_node_id)
    hit_points = np.stack([hit.point for hit in dominant_hits], axis=0)
    center_pt = np.mean(hit_points, axis=0)
    normal_vec = dominant_hits[0].normal

    if len(support_counts) > 1:
        uncertainty_reasons.append("mixed support surfaces intersected near object boundary")
    elif len(dominant_hits) < sample_point_count:
        uncertainty_reasons.append("partial support coverage near surface edge or mesh boundary")

    if any(hit.is_grazing for hit in dominant_hits):
        uncertainty_reasons.append("camera ray grazing angle exceeds 75 degrees from surface normal")

    support_type = "lidar_surface" if _lidar_verified(camera, dominant_hits, depth_buffer) else "roomplan_plane"
    if support_type == "roomplan_plane":
        uncertainty_reasons.append("attached to inferred RoomPlan plane; not independently verified by LiDAR")

    local_anchor = to_local(center_pt, support_node)
    sockets = _socket_targets(detection, camera, support_node)
    observed_region = _observed_region(detection, camera, support_node)
    localization_quality = "verified_support" if support_type == "lidar_surface" and not uncertainty_reasons else "needs_verification"
    dimensions, node_quality = _node_size(detection, support_type, observed_region, normal_vec, uncertainty_reasons)

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
        dimensions=dimensions,
        transform=Mat4(m=[
            rot_matrix[0, 0], rot_matrix[0, 1], rot_matrix[0, 2], float(center_pt[0]),
            rot_matrix[1, 0], rot_matrix[1, 1], rot_matrix[1, 2], float(center_pt[1]),
            rot_matrix[2, 0], rot_matrix[2, 1], rot_matrix[2, 2], float(center_pt[2]),
            0.0, 0.0, 0.0, 1.0,
        ]),
        quality=node_quality,
        movable=False,
        labeled_by="discovery",
        parent_id=support_node.id,
        relation="mounted_on",
        attachment=attachment,
    )
    return attachment, node


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
    unoccluded_hits = [hit for hit in all_hits if not hit.is_occluded]
    uncertainty_reasons: list[str] = []
    if len(all_hits) > 0 and len(unoccluded_hits) == 0:
        uncertainty_reasons.append("detection is fully occluded by foreground geometry")

    support_counts, dominant_node_id, dominant_hits = _dominant_support(unoccluded_hits)

    obs_crop = ObservationCrop(
        frame_id=detection.frame_id,
        sensor_box=list(detection.box),
        confidence=detection.confidence,
        image_url=image_url,
    )

    if len(dominant_hits) >= MIN_SUPPORT_RAYS and dominant_node_id is not None:
        return _anchored_attachment(
            detection, camera, graph, dominant_node_id, dominant_hits, support_counts,
            len(sample_points), uncertainty_reasons, obs_crop, depth_buffer,
        )
    return _unanchored_candidate(detection, camera, obs_crop, uncertainty_reasons)


def _unanchored_candidate(
    detection: Detection,
    camera: PhotoCamera,
    obs_crop: ObservationCrop,
    uncertainty_reasons: list[str],
) -> tuple[SurfaceAttachment, SceneNode]:
    """A candidate with no reliable measured surface: its position is not asserted."""
    uncertainty_reasons.append("no support surface intersected by sufficient rays; unanchored candidate")
    if not detection.is_outlet:
        uncertainty_reasons.append(
            "candidate has no measured size; zero display extent must not be read as device geometry"
        )
    attachment = SurfaceAttachment(
        support_node_id=None,
        support_type="unanchored",
        observations=[obs_crop],
        identity_confidence=detection.confidence,
        localization_quality="unanchored",
        review_status="candidate",
        uncertainty_reasons=uncertainty_reasons,
    )
    cam_pos = camera.position
    est_pt = cam_pos + camera.forward * 1.5
    stable_seed = f"unanchored_{detection.frame_id}_{round(float(detection.box[0]), 1)}_{round(float(detection.box[1]), 1)}"
    node_id = uuid.uuid5(uuid.NAMESPACE_OID, stable_seed)
    node = SceneNode(
        id=node_id,
        kind=_candidate_kind(detection),
        label=f"Candidate {taxonomy.semantic_class(detection.class_key).label.lower()} ({detection.name})",
        raw_category=detection.name,
        dimensions=_surface_dimensions(detection, [], np.zeros(3)),
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

    # Fallback: unanchored candidate
    uncertainty_reasons.append("no support surface intersected by sufficient rays; unanchored candidate")
    if not detection.is_outlet:
        uncertainty_reasons.append(
            "candidate has no measured size; zero display extent must not be read as device geometry"
        )
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
        dimensions=_surface_dimensions(detection, [], np.zeros(3)),
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
