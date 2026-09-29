"""What a real phone scan draws: the room and what stands in it, not boxes round unclaimed LiDAR or a lid over it."""

import json
import pathlib

import pytest
from standardphysics_contracts import SceneGraph, SceneNode, lies_flat, measured_as, stands_upright
from standardphysics_pipeline.blender import display_graph
from standardphysics_pipeline.discovery.mesh_surfaces import segment_surfaces
from standardphysics_pipeline.ingest import parse_room_json
from standardphysics_pipeline.lidar import room_faces
from standardphysics_pipeline.occupancy import UNCLAIMED_SURFACE

PHONE = pathlib.Path(__file__).parent.parent / "datasets/phone"
SCANS = ["test1", "ravida"]


def _scanned(name: str) -> SceneGraph:
    scan = PHONE / name
    graph = parse_room_json(json.loads((scan / "room.json").read_text()))
    surfaces = segment_surfaces(room_faces(scan / "lidar-mesh.json", graph.capture_to_room), graph)
    return graph.model_copy(update={"nodes": surfaces.nodes})


def _bottom(node: SceneNode) -> float:
    return node.transform.position.z - measured_as(node).z / 2


def _wall_middle(graph: SceneGraph) -> float:
    return max(node.transform.position.z for node in graph.nodes if stands_upright(node))


def _lids(graph: SceneGraph, middle: float) -> list[SceneNode]:
    return [node for node in graph.nodes if lies_flat(node) and _bottom(node) > middle]


@pytest.mark.parametrize("name", SCANS)
def test_a_real_scan_draws_no_box_round_unclaimed_lidar_and_no_lid_over_the_room(name):
    scanned = _scanned(name)
    middle = _wall_middle(scanned)
    assert any(node.raw_category == UNCLAIMED_SURFACE for node in scanned.nodes) and _lids(scanned, middle)

    drawn = display_graph(scanned)

    assert not [node for node in drawn.nodes if node.raw_category == UNCLAIMED_SURFACE]
    assert not _lids(drawn, middle)
    kept = {node.id for node in scanned.nodes if node.raw_category != UNCLAIMED_SURFACE and node not in _lids(scanned, middle)}
    assert {node.id for node in drawn.nodes} == kept
