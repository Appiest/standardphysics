"""Tests for Phase G secondary semantic corrections:
1. Photo-supported sofa/table label corrections (preserving raw_category, dimensions, collision).
2. Refusing to infer sofa identity just from length.
3. Whiteboard detection and wall attachment without fabricating missing ones.
4. Protecting owner corrections against automated overwrites.
5. Preserving obstacle and collision semantics.
"""

from __future__ import annotations

import uuid

import numpy as np
import pytest
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, SurfaceAttachment, Vec3
from standardphysics_pipeline.discovery.detect import Detection
from standardphysics_pipeline.discovery.semantic_corrections import (
    apply_secondary_semantic_corrections,
    correct_furniture_label,
    detect_and_attach_whiteboard,
)
from standardphysics_pipeline.textures.camera import PhotoCamera


def camera_at(position, looking_at, width=640, height=480, fx=500.0, fy=500.0) -> PhotoCamera:
    """A camera in room frame (+X right, +Y down, +Z forward)."""
    forward = np.asarray(looking_at, dtype=float) - np.asarray(position, dtype=float)
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    rotation = np.stack([right, down, forward])
    room_to_camera = np.vstack([
        np.hstack([rotation, (-rotation @ np.asarray(position, dtype=float)).reshape(3, 1)]),
        [0.0, 0.0, 0.0, 1.0],
    ])
    return PhotoCamera(
        frame_id="frame-001",
        room_to_camera=room_to_camera,
        fx=fx,
        fy=fy,
        cx=width / 2.0,
        cy=height / 2.0,
        width=width,
        height=height,
        timestamp=0.0,
    )


def test_sofa_label_correction_requires_photo_evidence_not_length():
    """An elongated box is not relabeled as sofa by length alone; requires photo detection."""
    table_id = uuid.uuid4()
    # 2.4m long table (sofa-like length)
    table_node = SceneNode(
        id=table_id,
        kind="object",
        label="Table",
        raw_category="table",
        dimensions=Vec3(x=2.4, y=0.9, z=0.8),
        transform=Mat4.translation(0.0, 2.0, 0.4),
        quality="measured",
        labeled_by="roomplan",
    )

    # Camera looking directly at the table at (0, 2, 0.4)
    cam = camera_at(position=[0.0, 0.0, 0.4], looking_at=[0.0, 2.0, 0.4])

    # 1. No detection -> No correction (length alone does NOT make it a sofa)
    # The function is strictly driven by photo detection
    # 2. Low-confidence detection (< 0.75) -> Rejected
    weak_det = Detection(
        frame_id="frame-001",
        name="sofa",
        box=(200.0, 150.0, 440.0, 330.0),
        movable=True,
        confidence=0.60,
    )
    assert correct_furniture_label(table_node, weak_det, cam) is None

    # 3. High-confidence photo detection (0.88) overlapping table projection
    strong_det = Detection(
        frame_id="frame-001",
        name="sofa",
        box=(200.0, 150.0, 440.0, 330.0),
        movable=True,
        confidence=0.88,
    )
    corrected = correct_furniture_label(table_node, strong_det, cam)
    assert corrected is not None
    assert corrected.label == "Sofa"
    assert corrected.labeled_by == "discovery"

    # Core invariant: raw_category, dimensions, and transform are preserved!
    assert corrected.raw_category == "table"
    assert corrected.dimensions == table_node.dimensions
    assert corrected.transform == table_node.transform


def test_owner_corrections_are_protected_against_automated_overwrites():
    """Owner edits must never be overwritten by automated discovery."""
    node_id = uuid.uuid4()
    owner_table = SceneNode(
        id=node_id,
        kind="object",
        label="Custom Table",
        raw_category="table",
        dimensions=Vec3(x=1.5, y=0.8, z=0.75),
        transform=Mat4.translation(0.0, 2.0, 0.375),
        quality="measured",
        labeled_by="owner",  # User edited this label!
    )

    cam = camera_at(position=[0.0, 0.0, 0.375], looking_at=[0.0, 2.0, 0.375])
    strong_sofa_det = Detection(
        frame_id="frame-001",
        name="sofa",
        box=(200.0, 150.0, 440.0, 330.0),
        movable=True,
        confidence=0.95,
    )

    corrected = correct_furniture_label(owner_table, strong_sofa_det, cam)
    assert corrected is None, "Owner correction must not be overwritten by automated pass"


def test_confirmed_review_status_is_protected():
    """Confirmed review status on an attachment protects the node from relabeling."""
    node_id = uuid.uuid4()
    confirmed_node = SceneNode(
        id=node_id,
        kind="object",
        label="Verified Desk",
        raw_category="table",
        dimensions=Vec3(x=1.2, y=0.6, z=0.75),
        transform=Mat4.translation(0.0, 2.0, 0.375),
        attachment=SurfaceAttachment(
            support_type="roomplan_plane",
            review_status="confirmed_by_user",
        ),
    )

    cam = camera_at(position=[0.0, 0.0, 0.375], looking_at=[0.0, 2.0, 0.375])
    strong_det = Detection(
        frame_id="frame-001",
        name="sofa",
        box=(200.0, 150.0, 440.0, 330.0),
        movable=True,
        confidence=0.92,
    )

    assert correct_furniture_label(confirmed_node, strong_det, cam) is None


def test_whiteboard_attached_to_wall_without_fabricating_missing():
    """Whiteboard is attached to measured wall with confident photo evidence, never fabricated."""
    wall_id = uuid.uuid4()
    # Wall along X from -2 to +2 at Y = 2.0, Z from 0 to 3.0
    wall = SceneNode(
        id=wall_id,
        kind="wall",
        label="Office Wall",
        raw_category="wall",
        dimensions=Vec3(x=4.0, y=0.0, z=3.0),
        transform=Mat4.translation(0.0, 2.0, 1.5),
    )

    cam = camera_at(position=[0.0, 0.0, 1.5], looking_at=[0.0, 2.0, 1.5])

    # 1. Low confidence (< 0.70) -> Rejected (do not fabricate)
    weak_wb = Detection(
        frame_id="frame-001",
        name="whiteboard",
        box=(220.0, 160.0, 420.0, 320.0),
        movable=False,
        confidence=0.55,
    )
    assert detect_and_attach_whiteboard(wall, weak_wb, cam) is None

    # 2. Confident whiteboard detection -> Attached to wall
    strong_wb = Detection(
        frame_id="frame-001",
        name="whiteboard",
        box=(220.0, 160.0, 420.0, 320.0),
        movable=False,
        confidence=0.88,
    )
    whiteboard = detect_and_attach_whiteboard(wall, strong_wb, cam)
    assert whiteboard is not None
    assert whiteboard.kind == "whiteboard"
    assert whiteboard.label == "Whiteboard"
    assert whiteboard.parent_id == wall.id
    assert whiteboard.relation == "attached_to"
    assert whiteboard.attachment is not None
    assert whiteboard.attachment.support_type == "lidar_surface"
    assert whiteboard.attachment.support_node_id == wall.id
    assert len(whiteboard.attachment.observations) == 1
    assert whiteboard.attachment.observations[0].confidence == pytest.approx(0.88)


def test_whiteboard_does_not_alter_wall_collision_or_create_floor_obstacles():
    """Surface-attached whiteboards are excluded from solid obstacles in SceneGraph."""
    wall_id = uuid.uuid4()
    wall = SceneNode(
        id=wall_id,
        kind="wall",
        label="Wall",
        raw_category="wall",
        dimensions=Vec3(x=4.0, y=0.0, z=3.0),
        transform=Mat4.translation(0.0, 2.0, 1.5),
    )

    cam = camera_at(position=[0.0, 0.0, 1.5], looking_at=[0.0, 2.0, 1.5])
    strong_wb = Detection(
        frame_id="frame-001",
        name="whiteboard",
        box=(220.0, 160.0, 420.0, 320.0),
        movable=False,
        confidence=0.90,
    )
    whiteboard = detect_and_attach_whiteboard(wall, strong_wb, cam)
    assert whiteboard is not None

    graph = SceneGraph(
        scan_id=uuid.uuid4(),
        nodes=[wall, whiteboard],
    )

    # Obstacles must exclude surface-attached annotations (attachment is not None)
    obstacles = graph.obstacles()
    assert all(n.id != whiteboard.id for n in obstacles), "Surface-attached whiteboard must not be a solid obstacle"


def test_apply_secondary_semantic_corrections_end_to_end():
    """End-to-end integration: sofa relabeled, whiteboard attached, owner edit preserved."""
    wall_id = uuid.uuid4()
    wall = SceneNode(
        id=wall_id,
        kind="wall",
        label="North Wall",
        raw_category="wall",
        dimensions=Vec3(x=6.0, y=0.0, z=3.0),
        transform=Mat4.translation(0.0, 3.0, 1.5),
    )

    table_id = uuid.uuid4()
    table = SceneNode(
        id=table_id,
        kind="object",
        label="Table",
        raw_category="table",
        dimensions=Vec3(x=2.0, y=1.0, z=0.8),
        transform=Mat4.translation(-1.0, 1.5, 0.4),
    )

    owner_id = uuid.uuid4()
    owner_desk = SceneNode(
        id=owner_id,
        kind="object",
        label="My Desk",
        raw_category="table",
        dimensions=Vec3(x=1.2, y=0.6, z=0.75),
        transform=Mat4.translation(1.5, 1.5, 0.375),
        labeled_by="owner",
    )

    graph = SceneGraph(
        scan_id=uuid.uuid4(),
        revision=1,
        nodes=[wall, table, owner_desk],
    )

    from dataclasses import replace
    cam1 = camera_at(position=[-1.0, 0.0, 0.4], looking_at=[-1.0, 1.5, 0.4])
    cam1 = replace(cam1, frame_id="frame-table")

    cam2 = camera_at(position=[0.0, 1.0, 1.5], looking_at=[0.0, 3.0, 1.5])
    cam2 = replace(cam2, frame_id="frame-wall")

    detections_by_frame = {
        "frame-table": [
            Detection(frame_id="frame-table", name="sofa", box=(200.0, 150.0, 440.0, 330.0), movable=True, confidence=0.85),
            Detection(frame_id="frame-table", name="sofa", box=(200.0, 150.0, 440.0, 330.0), movable=True, confidence=0.90),
        ],
        "frame-wall": [
            Detection(frame_id="frame-wall", name="whiteboard", box=(220.0, 160.0, 420.0, 320.0), movable=False, confidence=0.82),
        ],
    }

    updated_graph = apply_secondary_semantic_corrections(
        graph,
        detections_by_frame=detections_by_frame,
        cameras=[cam1, cam2],
    )

    # 1. Table was corrected to Sofa
    updated_table = updated_graph.by_id(table_id)
    assert updated_table.label == "Sofa"
    assert updated_table.raw_category == "table"
    assert updated_table.labeled_by == "discovery"

    # 2. Owner desk was untouched
    updated_owner = updated_graph.by_id(owner_id)
    assert updated_owner.label == "My Desk"
    assert updated_owner.labeled_by == "owner"

    # 3. Whiteboard was attached
    wb_nodes = [n for n in updated_graph.nodes if n.kind == "whiteboard"]
    assert len(wb_nodes) == 1
    assert wb_nodes[0].parent_id == wall.id
    assert wb_nodes[0].attachment is not None
    assert wb_nodes[0].attachment.support_node_id == wall.id


def test_a_chair_tucked_against_a_table_is_not_renamed_a_table():
    """From most viewpoints a café chair sits inside the table's photo box; that box is about the table."""
    chair = SceneNode(id=uuid.uuid4(), kind="object", label="Chair", raw_category="chair",
                      dimensions=Vec3(x=0.45, y=0.5, z=0.85), transform=Mat4.translation(0.0, 2.0, 0.425),
                      quality="measured", labeled_by="roomplan")
    cam = camera_at(position=[0.0, 0.0, 0.8], looking_at=[0.0, 2.2, 0.6])
    table_box = (120.0, 120.0, 520.0, 420.0)
    table = Detection(frame_id="frame-001", name="table", box=table_box, movable=True, confidence=0.95)
    assert correct_furniture_label(chair, table, cam) is None

    graph = SceneGraph(scan_id=uuid.uuid4(), nodes=[chair])
    updated = apply_secondary_semantic_corrections(graph, {"frame-001": [table]}, [cam])
    assert updated.by_id(chair.id).label == "Chair"


def test_a_label_the_detector_also_confirms_outvotes_a_single_contrary_frame():
    from dataclasses import replace

    table = SceneNode(id=uuid.uuid4(), kind="object", label="Table", raw_category="table",
                      dimensions=Vec3(x=2.0, y=1.0, z=0.8), transform=Mat4.translation(-1.0, 1.5, 0.4))
    box = (200.0, 150.0, 440.0, 330.0)
    frames = [replace(camera_at(position=[-1.0, 0.0, 0.4], looking_at=[-1.0, 1.5, 0.4]), frame_id=f"f{i}")
              for i in range(3)]
    detections = {
        "f0": [Detection(frame_id="f0", name="sofa", box=box, movable=True, confidence=0.9)],
        "f1": [Detection(frame_id="f1", name="table", box=box, movable=True, confidence=0.9)],
        "f2": [Detection(frame_id="f2", name="table", box=box, movable=True, confidence=0.9)],
    }
    updated = apply_secondary_semantic_corrections(SceneGraph(scan_id=uuid.uuid4(), nodes=[table]), detections, frames)
    assert updated.by_id(table.id).label == "Table"
