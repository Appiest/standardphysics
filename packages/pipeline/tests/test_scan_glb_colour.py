"""The painted scan stores the colours the photos recorded, in the colour space glTF says it holds."""

from __future__ import annotations

import json
import pathlib
import struct

import numpy as np
import pytest
from standardphysics_pipeline.check_blender import blender_path
from standardphysics_pipeline.coords import capture_to_room
from standardphysics_pipeline.textures.project import to_linear
from standardphysics_pipeline.textures.scan_colour import ColouredScan, scan_geometry, write_scan_glb

REPO = pathlib.Path(__file__).resolve().parents[3]
COMPONENT = {5126: ("f", 4, 1.0), 5123: ("H", 2, 65535.0), 5121: ("B", 1, 255.0)}


def _blender_missing() -> bool:
    try:
        blender_path()
    except (FileNotFoundError, RuntimeError):
        return True
    return False


def _vertex_colours(glb: pathlib.Path) -> np.ndarray:
    data = glb.read_bytes()
    json_length = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20:20 + json_length])
    binary = data[20 + json_length + 8:]
    accessor = document["accessors"][document["meshes"][0]["primitives"][0]["attributes"]["COLOR_0"]]
    view = document["bufferViews"][accessor["bufferView"]]
    code, size, scale = COMPONENT[accessor["componentType"]]
    width = {"VEC3": 3, "VEC4": 4}[accessor["type"]]
    stride = view.get("byteStride", size * width)
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    rows = [struct.unpack_from(f"<{width}{code}", binary, start + index * stride) for index in range(accessor["count"])]
    return np.asarray(rows, dtype=np.float64)[:, :3] / scale


@pytest.mark.skipif(_blender_missing(), reason="Blender not installed")
def test_a_photographed_grey_is_stored_as_the_linear_value_the_viewer_expects(tmp_path):
    vertices, triangles = scan_geometry(REPO / "datasets/phone/test1/lidar-mesh.json", capture_to_room(0.0))
    kept = triangles[:2000]
    used = np.unique(kept)
    remap = np.full(len(vertices), -1)
    remap[used] = np.arange(len(used))
    srgb_grey = 0.5
    scan = ColouredScan(
        vertices=vertices[used], triangles=remap[kept],
        colours=np.full((len(used), 3), srgb_grey, dtype=np.float32), seen=np.ones(len(used), bool),
    )

    stored = _vertex_colours(write_scan_glb(scan, tmp_path / "scan.glb"))

    assert stored == pytest.approx(float(to_linear(np.array(srgb_grey))), abs=0.01)
