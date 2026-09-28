"""Reading the LiDAR mesh into the room frame, and refusing when there is no room frame."""

import json

import pytest
from standardphysics_pipeline.coords import capture_to_room
from standardphysics_pipeline.lidar import LidarMeshError, room_cloud

IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


@pytest.fixture
def mesh(tmp_path):
    path = tmp_path / "lidar-mesh.json"
    part = {"transform": IDENTITY, "vertices": [0, 0, 0, 1, 0, 0, 0, 1, 0], "triangles": [0, 1, 2]}
    path.write_text(json.dumps({"parts": [part]}))
    return path


def test_a_mesh_reads_into_the_room_frame(mesh):
    assert len(room_cloud(mesh, capture_to_room(0.0), voxel=0.01)) == 3


def test_a_scan_with_no_room_frame_is_refused_as_a_mesh_error(mesh):
    """Discovery reports a LidarMeshError as a mesh it could not read. A graph
    without capture_to_room has to reach it the same way rather than as an
    AttributeError."""
    with pytest.raises(LidarMeshError):
        room_cloud(mesh, None)
