"""Objects drawn from their own scanned surface, on a real capture.

The box model drew every RoomPlan table as the same four-legged slab and every
sofa with a back and two arms, whatever the room held. These run the shape cut
against a real living-room walk, its RoomPlan boxes and its LiDAR mesh, to
check that what is drawn comes from the scan and never leaves the measured box.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import struct

import numpy as np
import pytest
from standardphysics_pipeline import parse_room_json
from standardphysics_pipeline.blender import export_glb
from standardphysics_pipeline.lidar import load_mesh, streamed_parts
from standardphysics_pipeline.object_shapes import scanned_shapes_from

CAPTURE = pathlib.Path(__file__).resolve().parents[3] / "datasets" / "replays" / "living-room"
TEMPLATE_VERTICES = 4 * 24
"""A sofa stand-in is four boxes, and Blender writes a box as 24 corners."""

pytestmark = pytest.mark.skipif(
    not (CAPTURE / "lidar-mesh.json").is_file(),
    reason="the recorded capture is not in this checkout",
)


@pytest.fixture(scope="module")
def room():
    return parse_room_json(json.loads((CAPTURE / "room.json").read_bytes()))


@pytest.fixture(scope="module")
def shapes(room):
    return scanned_shapes_from(room, CAPTURE / "lidar-mesh.json")


def signed_volume(shape) -> float:
    corners = shape.vertices[shape.faces]
    return float(np.einsum("ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])).sum() / 6)


def glb_vertex_counts(path: pathlib.Path) -> dict[str, int]:
    """How many corners each named glTF node's mesh has, read from the file's JSON chunk."""
    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20:20 + length])
    counts = {}
    for node in document["nodes"]:
        if "mesh" in node:
            primitives = document["meshes"][node["mesh"]]["primitives"]
            counts[node["name"]] = sum(document["accessors"][p["attributes"]["POSITION"]]["count"] for p in primitives)
    return counts


class TestShapesComeFromTheScan:
    def test_most_of_the_furniture_the_scan_covers_gets_a_shape(self, room, shapes):
        assert len(shapes) >= len(room.contents()) // 2

    def test_nothing_is_drawn_outside_the_measured_box(self, room, shapes):
        for node in room.contents():
            shape = shapes.get(str(node.id))
            if shape is None:
                continue
            half = np.asarray(node.dimensions.as_tuple()) / 2
            assert np.all(np.abs(shape.vertices) <= half + 1e-6)

    def test_every_shape_is_a_solid_whose_faces_look_outward(self, room, shapes):
        by_id = {str(node.id): node for node in room.contents()}
        for node_id, shape in shapes.items():
            box = float(np.prod(by_id[node_id].dimensions.as_tuple()))
            assert 0 < signed_volume(shape) <= box * 1.0001


class TestStreamingTheMesh:
    def test_streamed_anchors_are_exactly_the_loaded_anchors(self):
        streamed = list(streamed_parts(CAPTURE / "lidar-mesh.json"))
        loaded = load_mesh(CAPTURE / "lidar-mesh.json").parts
        assert len(streamed) == len(loaded)
        for one, other in zip(streamed, loaded, strict=True):
            assert np.array_equal(one.transform, other.transform)
            assert np.array_equal(one.vertices, other.vertices)
            assert np.array_equal(one.triangles, other.triangles)


@pytest.mark.skipif(shutil.which("blender") is None, reason="Blender is not installed")
class TestTheBoxModel:
    def test_a_sofa_is_drawn_from_its_scan_rather_than_the_sofa_template(self, room, shapes, tmp_path):
        sofas = [node for node in room.contents() if node.raw_category == "sofa" and str(node.id) in shapes]
        longest = max(sofas, key=lambda node: max(node.dimensions.as_tuple()))
        templates = glb_vertex_counts(export_glb(room, tmp_path / "templates.glb"))
        scanned = glb_vertex_counts(export_glb(room, tmp_path / "scanned.glb", CAPTURE / "lidar-mesh.json"))
        assert templates[str(longest.id)] == TEMPLATE_VERTICES
        assert scanned[str(longest.id)] > TEMPLATE_VERTICES
