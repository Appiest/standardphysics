"""A plan export is geometry evidence, not a new reconstruction or an access bypass."""

from __future__ import annotations

import io
import json
import uuid
import xml.etree.ElementTree as ET
import zipfile

import pytest
from standardphysics_contracts import DisplayPart, DisplayReconstruction, Mat4, SceneGraph, SceneNode, Vec3

from conftest import drain
from standardphysics_api.architecture_export import build_architecture_zip


def _node(identifier: str, kind: str, dimensions: tuple[float, float, float], transform: list[float], **extra):
    return SceneNode(
        id=uuid.UUID(identifier), kind=kind, label=extra.pop("label", kind.title()), raw_category=kind,
        dimensions=Vec3(x=dimensions[0], y=dimensions[1], z=dimensions[2]), transform=Mat4(m=transform), **extra,
    )


def _rotated_plan() -> SceneGraph:
    # Wall one is horizontal. Wall two has a 90-degree Z rotation, and its opening
    # has zero depth, as RoomPlan portals commonly do.
    horizontal = _node(
        "00000000-0000-0000-0000-000000000001", "wall", (6, 0, 3),
        [1, 0, 0, 0, 0, 1, 0, -2, 0, 0, 1, 1.5, 0, 0, 0, 1],
    )
    rotated = _node(
        "00000000-0000-0000-0000-000000000002", "wall", (6, 0, 3),
        [0, -1, 0, 2, 1, 0, 0, 0, 0, 0, 1, 1.5, 0, 0, 0, 1],
    )
    opening = _node(
        "00000000-0000-0000-0000-000000000003", "opening", (1.2, 0, 2),
        [0, -1, 0, 2, 1, 0, 0, 0.5, 0, 0, 1, 1, 0, 0, 0, 1],
        parent_id=rotated.id, relation="cut_into",
    )
    escaped = _node(
        "00000000-0000-0000-0000-000000000004", "object", (1, 0.5, 1),
        [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0.5, 0, 0, 0, 1], label="Table <A & B>", movable=True,
    )
    return SceneGraph(scan_id=uuid.UUID("00000000-0000-0000-0000-000000000099"), revision=7,
                      nodes=[horizontal, rotated, opening, escaped])


def _contents(archive: bytes) -> tuple[str, dict]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        assert zipped.namelist() == ["architecture-plan.svg", "evidence-ledger.json"]
        svg = zipped.read("architecture-plan.svg").decode()
        ledger = json.loads(zipped.read("evidence-ledger.json"))
    return svg, ledger


def test_export_is_a_parseable_deterministic_z_up_plan_with_meter_scale():
    graph = _rotated_plan()
    first = build_architecture_zip(graph.scan_id, "Measured shop", graph)
    second = build_architecture_zip(graph.scan_id, "Measured shop", graph)
    assert first == second
    svg, ledger = _contents(first)
    ET.fromstring(svg)
    assert 'id="meter-grid"' in svg
    assert ">1 m</text>" in svg
    assert 'class="opening"' in svg
    assert 'mask="url(#cut-00000000-0000-0000-0000-000000000002)"' in svg
    assert 'fill="#000" stroke="#000" stroke-width="4"' in svg
    assert "Table &lt;A &amp; B&gt;" in svg
    rotated = next(node for node in ledger["nodes"] if node["id"].endswith("2"))
    points = rotated["generated_display_geometry"]["points"]
    assert max(point[0] for point in points) - min(point[0] for point in points) == pytest.approx(0)
    assert max(point[1] for point in points) - min(point[1] for point in points) == pytest.approx(6.0)
    portal = next(node for node in ledger["nodes"] if node["id"].endswith("3"))
    assert portal["generated_display_geometry"]["wall_opening_cut"]


def test_ledger_marks_raw_capture_unknown_without_revision_zero_source():
    graph = _rotated_plan()
    _, ledger = _contents(build_architecture_zip(graph.scan_id, "Measured shop", graph))
    node = next(item for item in ledger["nodes"] if item["id"].endswith("4"))
    assert node["raw_capture"]["status"] == "unknown"
    assert node["generated_display_geometry"]["display_only"] is True
    assert node["original_placement_rationale"] == "unknown"
    assert ledger["scene"]["coordinate_system"] == {
        "units": "m", "up_axis": "Z", "transform_layout": "row_major_4x4",
    }
    assert "certify exact centimetre" in ledger["measurement_notice"]


def test_ledger_separates_revision_zero_capture_from_current_layout_geometry():
    graph = _rotated_plan()
    captured_object = graph.by_id(uuid.UUID("00000000-0000-0000-0000-000000000004")).model_copy(
        update={
            "dimensions": Vec3(x=0.8, y=0.4, z=1),
            "transform": Mat4.translation(-1, 0, 0.5),
        }
    )
    capture = graph.model_copy(update={
        "revision": 0,
        "nodes": [captured_object if node.id == captured_object.id else node for node in graph.nodes],
    })
    capture_raw = capture.model_dump(mode="json")
    next(node for node in capture_raw["nodes"] if node["id"].endswith("4"))["placement_reason"] = "documented survey point"
    _, ledger = _contents(
        build_architecture_zip(graph.scan_id, "Measured shop", graph, source_capture=capture,
                               source_capture_raw_graph=capture_raw)
    )
    node = next(item for item in ledger["nodes"] if item["id"].endswith("4"))
    assert node["raw_capture"]["status"] == "captured_from_revision_0"
    assert node["raw_capture"]["dimensions_m"] == {"x": 0.8, "y": 0.4, "z": 1.0}
    assert node["current_revision_geometry"]["dimensions_m"] == {"x": 1.0, "y": 0.5, "z": 1.0}
    assert node["generated_display_geometry"]["generated_from"] == "current_scene_revision.transform_and_dimensions"
    assert node["original_placement_rationale"] == "documented survey point"


def test_reconstruction_is_kept_as_display_only_geometry_with_its_provenance():
    graph = _rotated_plan()
    object_id = uuid.UUID("00000000-0000-0000-0000-000000000004")
    reconstructed = graph.by_id(object_id).model_copy(update={"reconstruction": DisplayReconstruction(
        summary="Photo-informed tabletop shape", confidence=0.7, evidence_frame_ids=["frame-1"],
        parts=[DisplayPart(
            name="top", primitive="box", center=[0, 0, 0], size=[0.8, 0.4, 0.1],
            base_color="#c0a080", material="wood",
        )],
    )})
    graph = graph.model_copy(update={
        "nodes": [reconstructed if node.id == object_id else node for node in graph.nodes],
    })
    _, ledger = _contents(build_architecture_zip(graph.scan_id, "Measured shop", graph))
    node = next(item for item in ledger["nodes"] if item["id"] == str(object_id))
    assert node["display_reconstruction"]["display_only"] is True
    assert node["display_reconstruction"]["provenance"]["evidence_frame_ids"] == ["frame-1"]


def test_architecture_endpoint_is_guarded_and_only_exports_the_owner_scan(make_client, stranger):
    with make_client(seed=True) as client:
        drain(client)
        scan_id = client.get("/api/scans").json()["scans"][0]["id"]
        response = client.get(f"/api/scans/{scan_id}/architecture.zip")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/zip")
        svg, ledger = _contents(response.content)
        assert ledger["scene"]["scan_id"] == scan_id
        assert ledger["scene"]["graph_hash"]
        ET.fromstring(svg)
        assert stranger.get(f"/api/scans/{scan_id}/architecture.zip").status_code == 404


def test_architecture_endpoint_requires_a_session(make_client):
    with make_client(sign_in_as_owner=False) as client:
        assert client.get(f"/api/scans/{uuid.uuid4()}/architecture.zip").status_code == 401
