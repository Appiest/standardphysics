"""A small real Blender bake proves the photos, UVs, coverage and GLB stay connected."""

from __future__ import annotations

import json
import pathlib
import shutil
from uuid import uuid4

import numpy as np
import pytest
from PIL import Image
from standardphysics_contracts import Mat4, PoseRecord, SceneGraph, SceneNode, Vec3
from standardphysics_pipeline.blender import glb_node_names
from standardphysics_pipeline.coords import capture_to_room
from standardphysics_pipeline.textures import bake as baker
from standardphysics_pipeline.textures.bake import (
    BakeInputs,
    TextureBakeError,
    _lidar_points,
    _load_images,
    bake_textures,
)
from standardphysics_pipeline.textures.camera import PhotoCamera
from standardphysics_pipeline.textures.project import TopViews


@pytest.mark.skipif(
    not shutil.which("blender") and not pathlib.Path("/Applications/Blender.app/Contents/MacOS/Blender").exists(),
    reason="Blender is required",
)
def test_bake_embeds_a_photo_atlas_and_writes_coverage(tmp_path):
    """A red camera looking at a wall produces a non-empty embedded texture build."""
    wall_id = uuid4()
    graph = SceneGraph(
        scan_id=uuid4(),
        capture_to_room=capture_to_room(-1.3),
        nodes=[SceneNode(
            id=wall_id, kind="wall", label="wall", raw_category="wall",
            dimensions=Vec3(x=2.0, y=0.08, z=2.0),
            transform=Mat4(m=[1, 0, 0, 0, 0, 1, 0, 2.5, 0, 0, 1, 1.3, 0, 0, 0, 1]),
        )],
    )
    frame = tmp_path / "frame.jpg"
    Image.fromarray(np.full((128, 128, 3), [220, 30, 20], dtype=np.uint8)).save(frame)
    camera_to_world = np.eye(4)
    camera_to_world[:3, 3] = [0, 1.3, 0]
    pose = PoseRecord(
        metadata_version=2, frame_id="frame-0001", image="frame.jpg", timestamp=1.0,
        transform=camera_to_world.T.reshape(-1).tolist(),
        intrinsics=[100, 0, 0, 0, 100, 0, 63.5, 63.5, 1], orientation="portrait",
        image_width=128, image_height=128, calibration_width=128, calibration_height=128,
        image_orientation="sensor",
    )
    poses = tmp_path / "poses.json"
    poses.write_text(json.dumps([pose.model_dump()]))

    result = bake_textures(BakeInputs(graph, poses, {"frame-0001": frame}, None, tmp_path / "out"))

    assert result.glb_path.is_file()
    assert [path.name for path in result.coverage_mask_paths] == ["coverage-0.png"]
    assert np.asarray(Image.open(result.coverage_mask_paths[0])).max() == 255
    assert glb_node_names(result.glb_path).count(str(wall_id)) == 1
    assert result.coverage.nodes[0].textured_fraction > 0.05


def test_extensionless_uploaded_lidar_mesh_uses_column_major_part_and_capture_transforms(tmp_path):
    """The phone's JSON mesh is ARKit-local; object storage need not preserve .json."""
    artifact = tmp_path / "lidar-mesh"
    artifact.write_text(json.dumps({
        "floorY": -1.3,
        "parts": [{
            "id": str(uuid4()),
            "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 10, 20, 30, 1],
            "vertices": [1, 2, 3, 2, 2, 3, 1, 3, 3],
            "triangles": [0, 1, 2],
        }],
    }))

    points = _lidar_points(artifact, capture_to_room(-1.3).m, tmp_path)

    assert np.allclose(points[0], [11, -33, 23.3])


def test_photo_dimensions_must_match_pose_then_are_capped_at_2048(tmp_path):
    frame = tmp_path / "frame"
    Image.new("RGB", (4096, 1024), (120, 130, 140)).save(frame, format="JPEG")
    camera = PhotoCamera("frame-0001", np.eye(4), 100, 100, 2047.5, 511.5, 4096, 1024, 1)

    images, resized = _load_images([camera], {"frame-0001": frame})

    assert images[0].shape[:2] == (512, 2048)
    assert (resized[0].width, resized[0].height) == (2048, 512)
    wrong = PhotoCamera("frame-0001", np.eye(4), 100, 100, 1023.5, 511.5, 2048, 1024, 1)
    with pytest.raises(TextureBakeError, match="pose metadata"):
        _load_images([wrong], {"frame-0001": frame})


def test_conflicting_photo_colours_stay_neutral_and_uncovered():
    views = TopViews(1)
    views.add(np.array([0]), np.array([1.0], dtype=np.float32), np.array([[1.0, 0.0, 0.0]], dtype=np.float32))
    views.add(np.array([0]), np.array([1.0], dtype=np.float32), np.array([[0.0, 0.0, 1.0]], dtype=np.float32))

    _, covered = views.resolve()

    assert not covered[0]


def test_vertex_only_lidar_npz_is_rejected_instead_of_inventing_faces(tmp_path):
    mesh = tmp_path / "lidar.npz"
    np.savez(mesh, points=np.zeros((6, 3), dtype=np.float32))

    with pytest.raises(TextureBakeError, match="triangle faces"):
        baker._lidar_triangles(mesh, capture_to_room(0).m, tmp_path)


def test_oversize_lidar_mesh_is_rejected_instead_of_being_thinned(tmp_path, monkeypatch):
    mesh = tmp_path / "lidar-mesh"
    mesh.write_text(json.dumps({
        "parts": [{
            "id": str(uuid4()),
            "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
            "vertices": [0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0],
            "triangles": [0, 1, 2, 1, 3, 2],
        }],
    }))
    monkeypatch.setattr(baker, "MAX_LIDAR_TRIANGLES", 1)

    with pytest.raises(TextureBakeError, match="maximum supported"):
        baker._lidar_triangles(mesh, capture_to_room(0).m, tmp_path)
