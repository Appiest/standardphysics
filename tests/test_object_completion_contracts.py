import json
from uuid import uuid4

import pytest
from pydantic import ValidationError
from standardphysics_contracts import DisplayPart, DisplayReconstruction, Mat4, SceneGraph, SceneNode, Vec3, graph_hash
from standardphysics_pipeline.mesh_evidence import object_mesh_profiles
from standardphysics_pipeline.textures.identity import bake_graph_for, stale_node_ids, texture_build_key


def completed_table():
    return DisplayReconstruction(
        summary="Round wood tabletop with a metal pedestal", confidence=0.8,
        evidence_frame_ids=["frame-0001"], parts=[
            DisplayPart(name="top", primitive="cylinder", center=[0, 0, 0.45], size=[1, 1, 0.1], base_color="#987654", material="wood"),
            DisplayPart(name="pedestal", primitive="cylinder", center=[0, 0, -0.05], size=[0.12, 0.12, 0.9], base_color="#333333", material="metal"),
        ],
    )


def table_graph():
    return SceneGraph(scan_id=uuid4(), capture_to_room=Mat4.identity(), nodes=[SceneNode(
        id=uuid4(), kind="object", label="Table", raw_category="table", dimensions=Vec3(x=2, y=1, z=1),
        transform=Mat4.translation(1, 2, 0.5), movable=True,
    )])


@pytest.mark.parametrize("patch", [
    {"center": [0.5, 0, 0]}, {"size": [0, 0.1, 0.1]},
    {"size": [float("nan"), 1, 1]}, {"center": [0, float("inf"), 0]},
    {"primitive": "python"}, {"bevel": 1},
])
def test_display_completion_rejects_invalid_or_unbounded_parts(patch):
    payload = completed_table().parts[0].model_dump() | patch
    with pytest.raises(ValidationError):
        DisplayPart.model_validate(payload)


def test_completion_changes_display_cache_but_not_measured_layout():
    graph = table_graph()
    completed = graph.model_copy(deep=True)
    completed.nodes[0].reconstruction = completed_table()
    assert graph_hash(completed) == graph_hash(graph)
    assert completed.nodes[0].dimensions == graph.nodes[0].dimensions
    assert completed.nodes[0].transform == graph.nodes[0].transform
    assert texture_build_key(completed, "photos") != texture_build_key(graph, "photos")
    assert stale_node_ids(completed, graph) == [graph.nodes[0].id]
    moved = completed.model_copy(deep=True)
    moved.nodes[0].transform.m[3] += 2
    assert texture_build_key(bake_graph_for(moved, graph), "photos") == texture_build_key(completed, "photos")
    assert stale_node_ids(moved, completed) == []


def test_partial_mesh_hints_use_node_local_coordinates_without_filling_holes(tmp_path):
    graph = table_graph()
    mesh = tmp_path / "mesh.json"
    mesh.write_text(json.dumps({"parts": [{
        "id": str(uuid4()), "transform": [1,0,0,0,0,1,0,0,0,0,1,0,1,2,0.5,1],
        "vertices": [0,0,0.45,0.01,0,0.45,0,0.01,0.45], "triangles": [0,1,2],
    }]}))
    profile = object_mesh_profiles(graph, mesh)[str(graph.nodes[0].id)]
    assert profile["sample_count"] == 3
    assert any("#" in row for row in profile["slices_bottom_to_top"][-1])
    assert all("#" not in row for level in profile["slices_bottom_to_top"][:-1] for row in level)
    assert "unobserved" in profile["legend"]
