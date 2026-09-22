"""Tests for multi-view reconciliation and owner decision preservation (MERGE-01 through MERGE-05).

Validates:
- MERGE-01: Two distinct consistent views of one faceplate produce one identity with both observations.
- MERGE-02: Nearby separate plates, opposite wall sides, and ambiguous A-B-C chains do not collapse.
- MERGE-03: Positive camera depth alone does not authorize merge when reprojection falls outside sensor box.
- MERGE-04: Owner rejection overrides detection and all uncertainty caveats survive merge.
- MERGE-05: Replaying observations in different permutations produces identical deterministic results.
"""

from __future__ import annotations

import uuid

import numpy as np
from standardphysics_contracts import Mat4, ObservationCrop, SceneNode, SurfaceAttachment, Vec3
from standardphysics_pipeline.discovery.reconcile import (
    are_compatible_observations,
    merge_two_nodes,
    reconcile_outlets,
)
from standardphysics_pipeline.textures.camera import PhotoCamera


def make_camera(pos, looking_at, frame_id="frame-0001", focal=500.0, width=640, height=480):
    forward = np.asarray(looking_at, dtype=float) - np.asarray(pos, dtype=float)
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    rotation = np.stack([right, down, forward])
    room_to_camera = np.vstack([
        np.hstack([rotation, (-rotation @ np.asarray(pos, dtype=float)).reshape(3, 1)]),
        [0.0, 0.0, 0.0, 1.0],
    ])
    return PhotoCamera(
        frame_id=frame_id,
        room_to_camera=room_to_camera,
        fx=focal,
        fy=focal,
        cx=width / 2.0,
        cy=height / 2.0,
        width=width,
        height=height,
        timestamp=0.0,
    )


def make_outlet_node(
    pos: tuple[float, float, float],
    normal: tuple[float, float, float] = (0.0, -1.0, 0.0),
    support_id: uuid.UUID | None = None,
    frame_id: str = "frame-0001",
    sensor_box: list[float] = [288.0, 216.0, 352.0, 264.0],
    review_status: str = "detected",
    uncertainty_reasons: list[str] = [],
    node_id: uuid.UUID | None = None,
) -> SceneNode:
    supp_id = support_id or uuid.uuid4()
    nid = node_id or uuid.uuid4()
    att = SurfaceAttachment(
        support_node_id=supp_id,
        support_type="lidar_surface",
        local_anchor=Vec3(x=0.0, y=0.0, z=0.0),
        normal=Vec3(x=normal[0], y=normal[1], z=normal[2]),
        observed_region=[],
        sockets=[],
        observations=[
            ObservationCrop(
                frame_id=frame_id,
                sensor_box=sensor_box,
                confidence=0.9,
            )
        ],
        identity_confidence=0.9,
        localization_quality="verified_support",
        review_status=review_status,
        uncertainty_reasons=uncertainty_reasons,
    )
    return SceneNode(
        id=nid,
        kind="outlet",
        label="Outlet",
        raw_category="outlet",
        dimensions=Vec3(x=0.12, y=0.03, z=0.12),
        transform=Mat4(m=[
            1.0, 0.0, 0.0, pos[0],
            0.0, 1.0, 0.0, pos[1],
            0.0, 0.0, 1.0, pos[2],
            0.0, 0.0, 0.0, 1.0,
        ]),
        quality="measured",
        movable=False,
        attachment=att,
    )


def test_merge_01_two_consistent_views_merge():
    """MERGE-01: Two distinct consistent views of one faceplate merge into one identity with both observations."""
    support_id = uuid.uuid4()
    cam1 = make_camera((0.0, 0.0, 1.0), (0.0, 2.0, 1.0), frame_id="frame-0001")
    cam2 = make_camera((0.8, 0.0, 1.0), (0.0, 2.0, 1.0), frame_id="frame-0002")

    # Both cameras point at the outlet region, so the observation boxes cover (320, 240)
    node1 = make_outlet_node((0.0, 2.0, 1.0), support_id=support_id, frame_id="frame-0001", sensor_box=[300, 220, 340, 260])
    node2 = make_outlet_node((0.02, 2.0, 1.01), support_id=support_id, frame_id="frame-0002", sensor_box=[300, 220, 340, 260])

    reconciled = reconcile_outlets([node1, node2], {"frame-0001": cam1, "frame-0002": cam2})

    assert len(reconciled) == 1
    merged = reconciled[0]
    assert len(merged.attachment.observations) == 2
    frame_ids = {obs.frame_id for obs in merged.attachment.observations}
    assert frame_ids == {"frame-0001", "frame-0002"}
    # Distance between cameras >= 0.5m -> independent viewpoints verified
    assert merged.attachment.localization_quality == "verified_support"
    assert not any("single viewpoint" in r for r in merged.attachment.uncertainty_reasons)


def test_merge_02_separate_plates_and_opposite_walls_do_not_collapse():
    """MERGE-02: Nearby separate plates, opposite wall sides and ambiguous A-B-C chain do not merge."""
    support_id = uuid.uuid4()

    # 1. Nearby separate plates (distance = 0.20m > 0.06m threshold)
    plate_a = make_outlet_node((0.0, 2.0, 1.0), support_id=support_id)
    plate_b = make_outlet_node((0.20, 2.0, 1.0), support_id=support_id)
    assert not are_compatible_observations(plate_a, plate_b)

    # 2. Opposite sides of a wall (normals opposite: -Y vs +Y)
    plate_front = make_outlet_node((0.0, 2.0, 1.0), normal=(0.0, -1.0, 0.0), support_id=support_id)
    plate_back = make_outlet_node((0.0, 2.1, 1.0), normal=(0.0, 1.0, 0.0), support_id=support_id)
    assert not are_compatible_observations(plate_front, plate_back)

    # 3. Ambiguous A-B-C chain: A is at 0, B is at 0.04m, C is at 0.08m
    # dist(A, B) = 0.04m <= 0.06m, dist(B, C) = 0.04m <= 0.06m, but dist(A, C) = 0.08m > 0.06m!
    node_a = make_outlet_node((0.0, 2.0, 1.0), support_id=support_id)
    node_b = make_outlet_node((0.04, 2.0, 1.0), support_id=support_id)
    node_c = make_outlet_node((0.08, 2.0, 1.0), support_id=support_id)

    reconciled = reconcile_outlets([node_a, node_b, node_c])
    # Must NOT collapse all 3 into 1 through transitive weak chain
    assert len(reconciled) >= 2


def test_merge_03_reprojection_mismatch_rejects_merge():
    """MERGE-03: Centers at (0,2,1) and (0.04,2,1) with projection outside recorded box [100,100,200,200] reject merge."""
    support_id = uuid.uuid4()
    cam1 = make_camera((0.0, 0.0, 1.0), (0.0, 2.0, 1.0), frame_id="frame-0001")

    # Camera 1 projects (0, 2, 1) to (320, 240).
    # But recorded sensor box in observation is [100, 100, 200, 200] (does NOT cover (320, 240)).
    node1 = make_outlet_node((0.0, 2.0, 1.0), support_id=support_id, frame_id="frame-0001", sensor_box=[100.0, 100.0, 200.0, 200.0])
    node2 = make_outlet_node((0.04, 2.0, 1.0), support_id=support_id, frame_id="frame-0001", sensor_box=[100.0, 100.0, 200.0, 200.0])

    # Compatibility check with camera should fail reprojection test
    assert not are_compatible_observations(node1, node2, camera_a=cam1)


def test_merge_04_owner_rejection_and_uncertainty_survive_merge():
    """MERGE-04: Owner rejection overrides detection; all caveats survive merge."""
    support_id = uuid.uuid4()

    # Node A is owner-rejected with caveat A
    node_a = make_outlet_node(
        (0.0, 2.0, 1.0),
        support_id=support_id,
        review_status="rejected_by_user",
        uncertainty_reasons=["wall edge nearby"],
    )
    # Node B is detected with caveat B
    node_b = make_outlet_node(
        (0.02, 2.0, 1.0),
        support_id=support_id,
        review_status="detected",
        uncertainty_reasons=["partial support coverage"],
    )

    merged = merge_two_nodes(node_a, node_b)
    # Rejection must be preserved
    assert merged.attachment.review_status == "rejected_by_user"
    # Both caveats must survive
    assert "wall edge nearby" in merged.attachment.uncertainty_reasons
    assert "partial support coverage" in merged.attachment.uncertainty_reasons

    # In reverse merge order, rejection and caveats still survive
    merged_rev = merge_two_nodes(node_b, node_a)
    assert merged_rev.attachment.review_status == "rejected_by_user"
    assert "wall edge nearby" in merged_rev.attachment.uncertainty_reasons
    assert "partial support coverage" in merged_rev.attachment.uncertainty_reasons


def test_merge_05_reconciliation_permutation_invariance():
    """MERGE-05: Replaying observations in different orders yields identical IDs and evidence.

    These three are near-identical positions seen in different frames with no
    camera evidence, so proximity must NOT merge them: each device's identity
    stays its own, and that decision is stable under every ordering.
    """
    support_id = uuid.uuid4()
    n1 = make_outlet_node((0.0, 2.0, 1.0), support_id=support_id, frame_id="frame-0001", node_id=uuid.UUID("11111111-1111-1111-1111-111111111111"))
    n2 = make_outlet_node((0.02, 2.0, 1.0), support_id=support_id, frame_id="frame-0002", node_id=uuid.UUID("22222222-2222-2222-2222-222222222222"))
    n3 = make_outlet_node((0.01, 2.0, 1.01), support_id=support_id, frame_id="frame-0003", node_id=uuid.UUID("33333333-3333-3333-3333-333333333333"))

    order1 = [n1, n2, n3]
    order2 = [n3, n1, n2]
    order3 = [n2, n3, n1]

    results = {
        "order1": reconcile_outlets(order1),
        "order2": reconcile_outlets(order2),
        "order3": reconcile_outlets(order3),
    }

    for name, reconciled in results.items():
        assert len(reconciled) == 3, f"{name} collapsed distinct devices"
        for node in reconciled:
            assert len(node.attachment.observations) == 1
            assert node.attachment.observations[0].frame_id in ("frame-0001", "frame-0002", "frame-0003")

    id_sets = {tuple(sorted(str(n.id) for n in reconciled)) for reconciled in results.values()}
    assert len(id_sets) == 1
    for node_id in {n.id for n in results["order1"]}:
        positions = {
            tuple(next(n for n in reconciled if n.id == node_id).transform.m)
            for reconciled in results.values()
        }
        assert len(positions) == 1


def test_merge_06_adjacent_outlets_never_merge_on_distance_alone():
    """MERGE-06: The real whiteboard run's pair: same support and normal, 4.77 cm apart,
    seen in different photos. Without camera verification they must stay two devices."""
    support_id = uuid.uuid4()
    plate_a = make_outlet_node(
        (-5.013, -0.426, 0.462), support_id=support_id,
        frame_id="frame-0309", sensor_box=[860.0, 700.0, 1100.0, 920.0],
    )
    plate_b = make_outlet_node(
        (-5.026, -0.430, 0.508), support_id=support_id,
        frame_id="frame-0407", sensor_box=[860.0, 700.0, 1100.0, 920.0],
    )

    assert not are_compatible_observations(plate_a, plate_b)
    first = reconcile_outlets([plate_a, plate_b])
    second = reconcile_outlets([plate_b, plate_a])
    assert len(first) == 2
    assert len(second) == 2
    assert {n.id for n in first} == {n.id for n in second}
    positions = sorted(
        (round(n.transform.m[3], 3), round(n.transform.m[7], 3), round(n.transform.m[11], 3))
        for n in first
    )
    assert positions == [(-5.026, -0.430, 0.508), (-5.013, -0.426, 0.462)]


def test_merge_07_same_photo_overlapping_boxes_of_one_faceplate_merge():
    """MERGE-07: Two detections in one photo covering the same faceplate agree visually
    and become one identity, with a single kept observation for that frame."""
    support_id = uuid.uuid4()
    node_a = make_outlet_node((0.0, 2.0, 1.0), support_id=support_id, frame_id="frame-0001", sensor_box=[288.0, 216.0, 352.0, 264.0])
    node_b = make_outlet_node((0.01, 2.0, 1.0), support_id=support_id, frame_id="frame-0001", sensor_box=[290.0, 218.0, 354.0, 266.0])

    assert are_compatible_observations(node_a, node_b)
    merged = merge_two_nodes(node_a, node_b)
    assert len(merged.attachment.observations) == 1
    assert merged.attachment.observations[0].frame_id == "frame-0001"


def test_merge_08_same_photo_disjoint_boxes_in_different_places_stay_separate():
    """MERGE-08: Nearby devices whose photo boxes barely overlap carry no visual
    agreement and stay separate even inside the faceplate distance tolerance."""
    support_id = uuid.uuid4()
    node_a = make_outlet_node((0.0, 2.0, 1.0), support_id=support_id, frame_id="frame-0001", sensor_box=[200.0, 200.0, 260.0, 260.0])
    node_b = make_outlet_node((0.04, 2.0, 1.0), support_id=support_id, frame_id="frame-0001", sensor_box=[400.0, 200.0, 460.0, 260.0])

    assert not are_compatible_observations(node_a, node_b)
