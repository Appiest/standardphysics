"""Tests for multi-view reconciliation, anti-transitivity, and preventing incorrect merges."""

from __future__ import annotations

import uuid

import numpy as np
import pytest
from standardphysics_contracts import Mat4, ObservationCrop, SceneNode, SocketTarget, SurfaceAttachment, Vec3
from standardphysics_pipeline.discovery.reconcile import (
    are_compatible_observations,
    merge_two_nodes,
    reconcile_outlets,
)


def outlet_node(
    centre: tuple[float, float, float],
    normal: tuple[float, float, float],
    support_id: uuid.UUID,
    frame_id: str,
    sockets: list[tuple[float, float, float]] | None = None,
) -> SceneNode:
    norm = np.array(normal, dtype=float)
    norm /= np.linalg.norm(norm)
    up = np.array([0.0, 0.0, 1.0])
    tangent = np.cross(norm, up)
    if np.linalg.norm(tangent) < 1e-4:
        tangent = np.array([1.0, 0.0, 0.0])
    else:
        tangent /= np.linalg.norm(tangent)
    bitangent = np.cross(norm, tangent)
    rot = np.column_stack([tangent, norm, bitangent])

    socket_targets = []
    if sockets:
        for i, (sx, sy, sz) in enumerate(sockets):
            socket_targets.append(SocketTarget(
                id=f"{frame_id}_s{i}",
                center=Vec3(x=sx, y=sy, z=sz),
                status="observed",
                confidence=0.9,
            ))

    att = SurfaceAttachment(
        support_node_id=support_id,
        support_type="lidar_surface",
        normal=Vec3(x=float(norm[0]), y=float(norm[1]), z=float(norm[2])),
        sockets=socket_targets,
        observations=[ObservationCrop(frame_id=frame_id, sensor_box=[200.0, 150.0, 400.0, 350.0], confidence=0.9)],
        localization_quality="verified_support",
    )

    return SceneNode(
        id=uuid.uuid4(),
        kind="outlet",
        label=f"Outlet ({frame_id})",
        raw_category="outlet",
        dimensions=Vec3(x=0.12, y=0.03, z=0.12),
        transform=Mat4(m=[
            rot[0, 0], rot[0, 1], rot[0, 2], float(centre[0]),
            rot[1, 0], rot[1, 1], rot[1, 2], float(centre[1]),
            rot[2, 0], rot[2, 1], rot[2, 2], float(centre[2]),
            0.0, 0.0, 0.0, 1.0,
        ]),
        parent_id=support_id,
        relation="mounted_on",
        attachment=att,
    )


class TestReconciliation:
    def test_same_photo_double_detection_merges_but_cross_frame_distance_never_does(self):
        wall_id = uuid.uuid4()
        # One photo may box the same faceplate twice; that visual agreement merges.
        n1 = outlet_node((1.0, 2.0, 0.4), (0.0, -1.0, 0.0), wall_id, "frame-01")
        n2 = outlet_node((1.01, 2.0, 0.41), (0.0, -1.0, 0.0), wall_id, "frame-01")
        assert are_compatible_observations(n1, n2) is True
        merged = reconcile_outlets([n1, n2])
        assert len(merged) == 1
        assert len(merged[0].attachment.observations) == 1
        assert merged[0].transform.m[3] == pytest.approx(1.005, abs=1e-3)

        # The same geometry seen in different photos carries no visual agreement,
        # so proximity must not merge it: uncertain counts stay unknown.
        n3 = outlet_node((1.0, 2.0, 0.4), (0.0, -1.0, 0.0), wall_id, "frame-02")
        n4 = outlet_node((1.01, 2.0, 0.41), (0.0, -1.0, 0.0), wall_id, "frame-03")
        assert are_compatible_observations(n3, n4) is False
        assert len(reconcile_outlets([n3, n4])) == 2

    def test_two_adjacent_outlets_do_not_merge(self):
        wall_id = uuid.uuid4()
        # Adjacent outlets separated by 15 cm along the wall (+X)
        n1 = outlet_node((1.0, 2.0, 0.4), (0.0, -1.0, 0.0), wall_id, "frame-01")
        n2 = outlet_node((1.15, 2.0, 0.4), (0.0, -1.0, 0.0), wall_id, "frame-02")
        assert are_compatible_observations(n1, n2) is False
        reconciled = reconcile_outlets([n1, n2])
        assert len(reconciled) == 2

    def test_opposite_sides_of_wall_do_not_merge(self):
        wall_id = uuid.uuid4()
        # Outlets back-to-back on opposite sides of a 10cm wall
        n1 = outlet_node((1.0, 1.95, 0.4), (0.0, -1.0, 0.0), wall_id, "frame-01")
        n2 = outlet_node((1.0, 2.05, 0.4), (0.0, 1.0, 0.0), wall_id, "frame-02") # Normal +Y
        assert are_compatible_observations(n1, n2) is False
        reconciled = reconcile_outlets([n1, n2])
        assert len(reconciled) == 2

    def test_different_support_walls_do_not_merge(self):
        wall1 = uuid.uuid4()
        wall2 = uuid.uuid4()
        n1 = outlet_node((1.0, 2.0, 0.4), (0.0, -1.0, 0.0), wall1, "frame-01")
        n2 = outlet_node((1.0, 2.0, 0.4), (0.0, -1.0, 0.0), wall2, "frame-02")
        assert are_compatible_observations(n1, n2) is False
        reconciled = reconcile_outlets([n1, n2])
        assert len(reconciled) == 2

    def test_reconcile_idempotence(self):
        wall_id = uuid.uuid4()
        n1 = outlet_node((1.0, 2.0, 0.4), (0.0, -1.0, 0.0), wall_id, "frame-01")
        n2 = outlet_node((1.01, 2.0, 0.41), (0.0, -1.0, 0.0), wall_id, "frame-02")
        n3 = outlet_node((2.0, 2.0, 0.4), (0.0, -1.0, 0.0), wall_id, "frame-03")
        once = reconcile_outlets([n1, n2, n3])
        twice = reconcile_outlets(once)
        # Without cameras or shared-photo agreement, none of these merge: three
        # distinct identities survive, and a second pass changes nothing.
        assert len(once) == len(twice) == 3
        once_xs = sorted(n.transform.m[3] for n in once)
        twice_xs = sorted(n.transform.m[3] for n in twice)
        assert once_xs == pytest.approx(twice_xs)

    def test_duplex_plate_retains_separate_sockets(self):
        wall_id = uuid.uuid4()
        # Duplex plate with two sockets: upper socket at Z=0.43, lower socket at Z=0.37 (6 cm apart)
        sockets_view1 = [(1.0, 2.0, 0.43), (1.0, 2.0, 0.37)]
        sockets_view2 = [(1.01, 2.0, 0.43), (1.01, 2.0, 0.37)]
        n1 = outlet_node((1.0, 2.0, 0.40), (0.0, -1.0, 0.0), wall_id, "frame-01", sockets=sockets_view1)
        n2 = outlet_node((1.01, 2.0, 0.40), (0.0, -1.0, 0.0), wall_id, "frame-02", sockets=sockets_view2)

        merged = merge_two_nodes(n1, n2)
        # Both sockets must be retained as distinct targets, not collapsed into one
        assert len(merged.attachment.sockets) == 2
        z_coords = sorted(s.center.z for s in merged.attachment.sockets)
        assert z_coords[0] == pytest.approx(0.37, abs=0.01)
        assert z_coords[1] == pytest.approx(0.43, abs=0.01)

    def test_power_strip_on_furniture_remains_distinct_from_wall_outlets(self):
        wall_id = uuid.uuid4()
        desk_id = uuid.uuid4()
        wall_outlet = outlet_node((1.0, 2.0, 0.40), (0.0, -1.0, 0.0), wall_id, "frame-01")
        power_strip = outlet_node((1.02, 2.0, 0.40), (0.0, -1.0, 0.0), desk_id, "frame-02")

        assert are_compatible_observations(wall_outlet, power_strip) is False
        reconciled = reconcile_outlets([wall_outlet, power_strip])
        assert len(reconciled) == 2

    def test_repeating_wall_texture_does_not_merge_distant_identical_outlets(self):
        wall_id = uuid.uuid4()
        # Two identical-looking outlets separated by 1.2 m along the same wall
        n1 = outlet_node((1.0, 2.0, 0.40), (0.0, -1.0, 0.0), wall_id, "frame-01")
        n2 = outlet_node((2.2, 2.0, 0.40), (0.0, -1.0, 0.0), wall_id, "frame-02")
        assert are_compatible_observations(n1, n2) is False
        reconciled = reconcile_outlets([n1, n2])
        assert len(reconciled) == 2

    def test_duplicate_video_frames_do_not_claim_independent_verification(self):
        from standardphysics_pipeline.textures.camera import PhotoCamera

        def cam(frame_id, pos, look_at):
            fwd = np.asarray(look_at, dtype=float) - np.asarray(pos, dtype=float)
            fwd /= np.linalg.norm(fwd)
            right = np.cross(fwd, [0.0, 0.0, 1.0])
            right /= np.linalg.norm(right)
            down = np.cross(fwd, right)
            rot = np.stack([right, down, fwd])
            return PhotoCamera(
                frame_id=frame_id,
                room_to_camera=np.vstack([
                    np.hstack([rot, (-rot @ np.asarray(pos, dtype=float)).reshape(3, 1)]),
                    [0.0, 0.0, 0.0, 1.0],
                ]),
                fx=500.0, fy=500.0, cx=319.5, cy=239.5, width=640, height=480, timestamp=0.0,
            )

        wall_id = uuid.uuid4()
        n1 = outlet_node((1.0, 2.0, 0.4), (0.0, -1.0, 0.0), wall_id, "frame-01")
        n2 = outlet_node((1.01, 2.0, 0.41), (0.0, -1.0, 0.0), wall_id, "frame-02")

        # Camera positions are only 4 cm apart (< MIN_INDEPENDENT_VIEW_DISTANCE_M = 0.50m)
        cameras = {
            "frame-01": cam("frame-01", (1.0, 0.0, 0.4), (1.0, 2.0, 0.4)),
            "frame-02": cam("frame-02", (1.04, 0.0, 0.4), (1.0, 2.0, 0.4)),
        }
        reconciled = reconcile_outlets([n1, n2], cameras=cameras)
        assert len(reconciled) == 1
        merged_att = reconciled[0].attachment
        # Single viewpoint uncertainty must be retained because cameras are near-duplicate frames
        assert any("single viewpoint" in r for r in merged_att.uncertainty_reasons)

    def test_inconsistent_camera_projection_rejects_merge(self):
        from standardphysics_pipeline.textures.camera import PhotoCamera

        def cam(frame_id, pos, look_at):
            fwd = np.asarray(look_at, dtype=float) - np.asarray(pos, dtype=float)
            fwd /= np.linalg.norm(fwd)
            right = np.cross(fwd, [0.0, 0.0, 1.0])
            right /= np.linalg.norm(right)
            down = np.cross(fwd, right)
            rot = np.stack([right, down, fwd])
            return PhotoCamera(
                frame_id=frame_id,
                room_to_camera=np.vstack([
                    np.hstack([rot, (-rot @ np.asarray(pos, dtype=float)).reshape(3, 1)]),
                    [0.0, 0.0, 0.0, 1.0],
                ]),
                fx=500.0, fy=500.0, cx=319.5, cy=239.5, width=640, height=480, timestamp=0.0,
            )

        # Camera 1 at (1.0, 0.0, 0.4) looking towards +Y at outlet at (1.0, 2.0, 0.4)
        cam1 = cam("frame-01", (1.0, 0.0, 0.4), (1.0, 2.0, 0.4))
        # Camera 2 at (1.0, 5.0, 0.4) looking towards +Y at (1.0, 6.0, 0.4) (so outlet at Y=2.0 is behind it, depth < 0)
        cam2 = cam("frame-02", (1.0, 5.0, 0.4), (1.0, 6.0, 0.4))

        wall_id = uuid.uuid4()
        n1 = outlet_node((1.0, 2.0, 0.4), (0.0, -1.0, 0.0), wall_id, "frame-01")
        n2 = outlet_node((1.01, 2.0, 0.4), (0.0, -1.0, 0.0), wall_id, "frame-02")

        # Because n1 is behind cam2, reprojection fails (depth <= 0) and merge is rejected
        assert are_compatible_observations(n1, n2, camera_a=cam1, camera_b=cam2) is False

