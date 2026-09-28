"""Secondary semantic corrections for Standard Physics (Phase G).

Reuses the evidence/attachment pattern for whiteboards and photo-supported sofa, table
and counter label corrections.

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
from collections import Counter
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

SEAT_NAMES = frozenset({"chair", "stool", "bar_stool", "seat", "high_chair", "armchair", "dining_chair"})

COUNTER_NAMES = frozenset({
    "counter",
    "service_counter",
    "sales_counter",
    "checkout_counter",
    "order_counter",
})

WORK_SURFACE_NAMES = TABLE_NAMES | COUNTER_NAMES
"""A table and a counter are one family: a raised top people stand or sit at.

A photo calls a café's bar a counter, and RoomPlan calls it a table; neither
is wrong, so a scanned table the photos call a counter keeps its name. What
the family does move is RoomPlan's storage box that every photo calls a
counter, because storage is not a work surface at all."""

RELABELS = ((SOFA_NAMES, "Sofa"), (TABLE_NAMES, "Table"), (COUNTER_NAMES, "Counter"))
"""Which detector names can rename a scanned piece, and the label each gives it."""

MIN_FURNITURE_CORRECTION_CONFIDENCE = 0.75
MIN_DETECTION_ON_NODE = 0.6
"""Share of a detection's box the node's own outline must cover before the detection names that node.

A café chair tucked against a table sits inside the table's box from most
viewpoints, but covers little of it; the table's box is about the table."""
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


def _projected_box(node: SceneNode, camera: PhotoCamera) -> tuple[float, float, float, float] | None:
    """The node's eight corners on the image, clipped to it; None when any corner is behind the camera."""
    matrix = np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
    half = np.array([node.dimensions.x, node.dimensions.y, node.dimensions.z]) / 2
    corners = [matrix @ np.array([sx * half[0], sy * half[1], sz * half[2], 1.0])
               for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    points = [_project_point(camera, corner[:3]) for corner in corners]
    seen = [point for point in points if point is not None]
    if len(seen) < len(points):
        return None
    cols, rows = [point[0] for point in seen], [point[1] for point in seen]
    return (max(min(cols), 0.0), max(min(rows), 0.0), min(max(cols), camera.width), min(max(rows), camera.height))


def _covered_share(detection_box: Sequence[float], node_box: Sequence[float]) -> float:
    """How much of the detection's box the node's outline covers."""
    left, top = max(detection_box[0], node_box[0]), max(detection_box[1], node_box[1])
    right, bottom = min(detection_box[2], node_box[2]), min(detection_box[3], node_box[3])
    area = (detection_box[2] - detection_box[0]) * (detection_box[3] - detection_box[1])
    return max(0.0, right - left) * max(0.0, bottom - top) / area if area > 0 else 0.0


def _names(node: SceneNode, detection: Detection, camera: PhotoCamera) -> bool:
    """Whether the detection is a picture of this node rather than of something it stands beside."""
    node_box = _projected_box(node, camera)
    return node_box is not None and _covered_share(_detection_box(detection), node_box) >= MIN_DETECTION_ON_NODE


def _key(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def is_work_surface(name: str) -> bool:
    """A table or a counter by name: something that stands on the floor and carries a top."""
    return _key(name) in WORK_SURFACE_NAMES


def same_furniture(first: str, second: str) -> bool:
    """Whether two names mean the same kind of furniture, so a stool agrees with a scanned chair."""
    return _family(first) == _family(second)


def _family(name: str) -> str:
    key = _key(name)
    for family, names in (("seat", SEAT_NAMES), ("table", WORK_SURFACE_NAMES), ("sofa", SOFA_NAMES)):
        if key in names:
            return family
    return key


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
    """Photo-supported sofa, table or counter label correction.

    A name of the node's own family is no correction: a scanned table the
    photo calls a counter is still a table. Preserves raw_category, measured
    dimensions, and transform.
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
    target_label = next((label for names, label in RELABELS if _key(name) in names), None)
    if target_label is None or same_furniture(name, node.label):
        return None

    if not _names(node, detection, camera):
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


BOARD_APART = 0.5
"""Boards whose centres are closer than this are one board."""


def _one_board_per_place(boards: list[SceneNode]) -> list[SceneNode]:
    """The best-evidenced board at each place on the walls.

    RoomPlan often measures one wall as several overlapping pieces, and the same
    photographed board lands on every one of them. A board is never itself a
    wall, so boards already attached are not offered as walls either.
    """
    kept: list[SceneNode] = []
    for board in sorted(boards, key=lambda one: -one.attachment.identity_confidence):
        where = np.asarray(board.transform.position.as_tuple())
        if all(np.linalg.norm(where - np.asarray(other.transform.position.as_tuple())) >= BOARD_APART for other in kept):
            kept.append(board)
    return kept


def _voted_label(node: SceneNode, detections_by_frame: dict[str, list[Detection]],
                 cameras_by_id: dict[str, PhotoCamera]) -> SceneNode:
    """The node relabeled only when the new name is what most frames call it.

    Each frame casts at most one vote per name, from confident detections that
    are pictures of this node. A name of the node's own family votes to keep
    it, so a chair the detector also calls a chair keeps its label. Every other
    name it is called votes against the change too: a storage box most photos
    call a recycling bin is not a counter because a few of them caught the
    counter's rectangle over it.
    """
    tally: Counter[str] = Counter()
    proposals: dict[str, SceneNode] = {}
    for frame_id, detections in detections_by_frame.items():
        camera = cameras_by_id.get(frame_id)
        if camera is not None:
            tally.update(_frame_votes(node, detections, camera, proposals))
    return _plurality(node, tally, proposals)


def _frame_votes(node: SceneNode, detections: list[Detection], camera: PhotoCamera,
                 proposals: dict[str, SceneNode]) -> set[str]:
    """The names this frame calls the node by: a proposed label, its own label, or another family."""
    node_box = _projected_box(node, camera)
    votes: set[str] = set()
    for detection in detections:
        corrected = correct_furniture_label(node, detection, camera)
        if corrected is not None:
            proposals.setdefault(corrected.label, corrected)
            votes.add(corrected.label)
        elif _pictures(node_box, detection):
            name = _detection_name(detection)
            votes.add(node.label if same_furniture(name, node.label) else _family(name))
    return votes


def _pictures(node_box: tuple[float, float, float, float] | None, detection: Detection) -> bool:
    return (
        node_box is not None
        and detection.confidence >= MIN_FURNITURE_CORRECTION_CONFIDENCE
        and _covered_share(_detection_box(detection), node_box) >= MIN_DETECTION_ON_NODE
    )


def _plurality(node: SceneNode, tally: Counter[str], proposals: dict[str, SceneNode]) -> SceneNode:
    if not proposals:
        return node
    winner = max(proposals, key=lambda label: tally[label])
    rivals = max((count for label, count in tally.items() if label != winner), default=0)
    return proposals[winner] if tally[winner] > rivals else node


MIN_BUILT_IN_VOTES = 2
"""Frames that must call a scanned piece fixed before it stops being movable."""


def _voted_built_in(node: SceneNode, detections_by_frame: dict[str, list[Detection]],
                    cameras_by_id: dict[str, PhotoCamera]) -> SceneNode:
    """A scanned piece most frames call fixed, under a name of its own family, stops being movable.

    RoomPlan files a service counter under storage, which starts movable, so the
    solver would turn a plumbed-in counter to clear a floor space. The vote only
    ever fixes a piece in place; it never frees one.
    """
    if not node.movable or bounds_the_room(node) or _is_owner_protected(node):
        return node
    fixed = movable = 0
    for frame_id, detections in detections_by_frame.items():
        camera = cameras_by_id.get(frame_id)
        vote = None if camera is None else _movability_vote(node, detections, camera)
        fixed += vote is False
        movable += vote is True
    if fixed >= MIN_BUILT_IN_VOTES and fixed > movable:
        return node.model_copy(update={"movable": False, "labeled_by": "discovery"})
    return node


def _movability_vote(node: SceneNode, detections: list[Detection], camera: PhotoCamera) -> bool | None:
    """What this frame says about moving the node: None when no picture of it names its family."""
    node_box = _projected_box(node, camera)
    says = {detection.movable for detection in detections
            if _pictures(node_box, detection) and same_furniture(_detection_name(detection), node.label)}
    return None if len(says) != 1 else says.pop()


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

    # 1. Check existing nodes for sofa/table corrections, by a vote across frames
    updated_nodes = [_voted_built_in(_voted_label(node, detections_by_frame, cameras_by_id),
                                     detections_by_frame, cameras_by_id) for node in graph.nodes]

    # 2. Check walls for whiteboard attachments: one board per wall,
    # the best-evidenced view, so many frames of one board are not many boards.
    whiteboards = _one_board_per_place([
        board
        for wall in updated_nodes
        if wall.attachment is None and reads_as_wall(wall)
        for board in [_best_whiteboard(wall, detections_by_frame, cameras_by_id)]
        if board is not None
    ])

    all_nodes = [*updated_nodes, *whiteboards]
    return graph.model_copy(update={
        "revision": graph.revision + (1 if (len(whiteboards) > 0 or any(n.labeled_by == "discovery" for n in updated_nodes)) else 0),
        "nodes": all_nodes,
    })
