"""A plan export is geometry evidence, not a new reconstruction or an access bypass."""

from __future__ import annotations

import io
import json
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime

import pytest
from standardphysics_contracts import (
    Assessment,
    Citation,
    DisplayPart,
    DisplayReconstruction,
    EvidenceBundle,
    Finding,
    Mat4,
    ObservationCrop,
    SceneGraph,
    SceneNode,
    ScopeItem,
    ScopeManifest,
    ScopeRow,
    SocketTarget,
    SurfaceAttachment,
    Vec3,
    graph_hash,
)

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


def test_architecture_export_includes_outlets_json_when_outlets_present():
    graph = _rotated_plan()
    wall = graph.nodes[0]
    outlet_id = uuid.UUID("00000000-0000-0000-0000-000000000010")
    outlet_node = SceneNode(
        id=outlet_id,
        kind="outlet",
        label="Wall outlet",
        raw_category="outlet",
        dimensions=Vec3(x=0.07, y=0.02, z=0.115),
        transform=Mat4(m=[1, 0, 0, 0, 0, 1, 0, -1.99, 0, 0, 1, 0.40, 0, 0, 0, 1]),
        parent_id=wall.id,
        relation="attached_to",
        attachment=SurfaceAttachment(
            support_type="lidar_surface",
            support_node_id=wall.id,
            normal=Vec3(x=0.0, y=1.0, z=0.0),
            review_status="detected",
            observations=[
                ObservationCrop(
                    frame_id="frame-01",
                    sensor_box=[100.0, 100.0, 200.0, 200.0],
                    confidence=0.92,
                    image_url="crops/crop-01.jpg",
                )
            ],
            sockets=[
                SocketTarget(id="socket-0", center=Vec3(x=0.0, y=-1.99, z=0.43)),
                SocketTarget(id="socket-1", center=Vec3(x=0.0, y=-1.99, z=0.37)),
            ],
        ),
    )
    graph_with_outlet = graph.model_copy(update={"nodes": [*graph.nodes, outlet_node]})
    zip_bytes = build_architecture_zip(graph_with_outlet.scan_id, "Shop with outlet", graph_with_outlet)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zipped:
        names = zipped.namelist()
        assert "architecture-plan.svg" in names
        assert "evidence-ledger.json" in names
        assert "outlets.json" in names

        svg = zipped.read("architecture-plan.svg").decode()
        assert '<circle class="outlet"' in svg
        assert '.outlet{fill:#e69f00' in svg

        ledger = json.loads(zipped.read("evidence-ledger.json"))
        node_entry = next(n for n in ledger["nodes"] if n["id"] == str(outlet_id))
        assert node_entry["attachment"]["support_type"] == "lidar_surface"
        assert node_entry["uncertainty"]["power_state"] == "unknown"
        assert len(node_entry["disclaimers"]) > 0

        outlets_data = json.loads(zipped.read("outlets.json"))
        assert outlets_data["format"] == "standardphysics.outlets-evidence.v1"
        assert len(outlets_data["outlets"]) == 1
        outlet_export = outlets_data["outlets"][0]
        assert outlet_export["id"] == str(outlet_id)
        assert outlet_export["uncertainty"]["power_state"] == "unknown"
        assert outlet_export["local_height_m"] == pytest.approx(0.40)
        assert len(outlet_export["attachment"]["sockets"]) == 2


def _scope(manifest_hash: str = "scope-manifest-hash") -> ScopeManifest:
    scan_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    return ScopeManifest(
        id=uuid.UUID("00000000-0000-0000-0000-0000000000ab"),
        scan_id=scan_id,
        version=2,
        created_at=datetime(2026, 9, 21, 12, 0, 0),
        graph_revision=7,
        graph_hash="scope-was-frozen-for-this-graph",
        rulepack_version="pilot-v1",
        manifest_hash=manifest_hash,
        surveyed_areas=["front"],
        unobserved_areas=["back room"],
        route_endpoints=["Entrance", "Counter"],
        requested_classes=["outlet", "service_counter"],
        requested_requirements=["route_clear_width", "counter_height"],
        applicability_questions=["Is the rear counter customer-facing?"],
        unresolved_questions=["Restroom fixtures were not surveyed."],
        rows=[
            ScopeRow(
                item=ScopeItem(item_slug="counter-main", item_kind="object", label="Main counter"),
                requirement_id="counter_height",
                applicability="applicable",
                applicability_reason="The counter serves customers.",
                applicability_facts=["owner confirmed the counter is customer-facing"],
                outcome="satisfied",
                reason="36 in measured at the accessible section.",
                evidence_refs=["measurement-31"],
                measurement={"method": "lidar", "value_in": 36.0},
                source_version="ADA_2010 904",
                legal_review_status="unreviewed_preview",
            ),
            ScopeRow(
                item=ScopeItem(item_slug="restroom-entrance", item_kind="class", label="Restroom entrance",
                               observed=False, source="requested_not_observed"),
                requirement_id="restroom_entrance_width",
                applicability="unknown",
                outcome="unobserved",
                reason="No restroom entrance was detected or marked.",
                evidence_refs=[],
                source_version="ADA_2010 404",
                legal_review_status="unreviewed_preview",
            ),
        ],
    )


def _assessment_for(over_graph: SceneGraph, scope: ScopeManifest | None = None) -> Assessment:
    graph_h = graph_hash(over_graph)
    return Assessment(
        id=uuid.uuid4(), scan_id=over_graph.scan_id, graph_revision=over_graph.revision,
        graph_hash=graph_h, rulepack_version="pilot-v1", pass_number=1,
        created_at=datetime(2026, 9, 21, 12, 1, 0), scope=scope,
        findings=[Finding(
            id=uuid.uuid4(), check_id="route_clear_width", outcome="question",
            title="The path to the counter is too narrow", detail="31 in at the tightest point.",
            measured_inches=31.0, required_inches=36.0,
            citation=Citation(authority="ADA_2010", edition="2010", section="403.5.1"),
        )],
    )


def test_export_carries_pinned_scope_evidence_and_scenario_identity():
    graph = _rotated_plan()
    scope = _scope()
    assessment = _assessment_for(graph, scope=scope)
    bundle = EvidenceBundle(version=2, manifest_hash="evidence-manifest-hash", complete=True)
    first = build_architecture_zip(
        graph.scan_id, "Measured shop", graph, assessment,
        scenario_name="Order a drink", scenario_version=3, evidence_bundle=bundle,
    )
    second = build_architecture_zip(
        graph.scan_id, "Measured shop", graph, assessment,
        scenario_name="Order a drink", scenario_version=3, evidence_bundle=bundle,
    )
    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as zipped:
        ledger = json.loads(zipped.read("evidence-ledger.json"))
    assert ledger["format"] == "standardphysics.architecture-evidence-ledger.v2"
    assert ledger["scene"]["graph_hash"] == graph_hash(graph)
    assert ledger["evidence_closure"] == {
        "bundle_version": 2, "evidence_manifest_hash": "evidence-manifest-hash", "complete": True,
        "note": ledger["evidence_closure"]["note"],
    }
    assert ledger["scenario"]["included"] is True
    assert ledger["scenario"]["pinned_version"] == 3
    assert ledger["scenario"]["name"] == "Order a drink"
    assert ledger["assessment"]["included"] is True
    assert ledger["assessment"]["stale_for_current_graph"] is False
    exported_scope = ledger["assessment"]["scope"]
    assert exported_scope["format"] == "standardphysics.scope-matrix.v1"
    assert exported_scope["manifest"]["manifest_hash"] == "scope-manifest-hash"
    assert exported_scope["manifest"]["version"] == 2
    assert exported_scope["unobserved_areas"] == ["back room"]
    assert exported_scope["unresolved_questions"] == ["Restroom fixtures were not surveyed."]
    assert len(exported_scope["rows"]) == 2
    row = exported_scope["rows"][0]
    assert row["item"]["item_slug"] == "counter-main"
    assert row["applicability"] == "applicable"
    assert row["applicability_facts"] == ["owner confirmed the counter is customer-facing"]
    assert row["outcome"] == "satisfied"
    assert row["evidence_refs"] == ["measurement-31"]
    assert row["measurement"]["method"] == "lidar"
    assert row["source_version"] == "ADA_2010 904"
    assert row["legal_review_status"] == "unreviewed_preview"
    unobserved = exported_scope["rows"][1]
    assert unobserved["outcome"] == "unobserved"
    assert unobserved["item"]["observed"] is False
    assert unobserved["item"]["source"] == "requested_not_observed"
    node = next(item for item in ledger["nodes"] if item["id"].endswith("4"))
    assert node["label_provenance"] == "roomplan"


def test_stale_assessment_is_named_and_never_applied_to_the_scene():
    graph = _rotated_plan()
    stale = Assessment(
        id=uuid.uuid4(), scan_id=graph.scan_id, graph_revision=6,
        graph_hash="a-different-graph", rulepack_version="pilot-v1", pass_number=1,
        created_at=datetime(2026, 9, 21, 12, 1, 0), scope=_scope(), findings=[],
    )
    archive = build_architecture_zip(graph.scan_id, "Measured shop", graph, stale)
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        ledger = json.loads(zipped.read("evidence-ledger.json"))
    assert ledger["assessment"]["included"] is False
    assert ledger["assessment"]["stale_for_current_graph"] is True
    assert ledger["assessment"]["findings"] == []
    assert "scope" not in ledger["assessment"]
    assert "stale_note" in ledger["assessment"]


def test_revision_pin_regenerates_the_old_export_byte_for_byte(make_client):
    with make_client(seed=True) as client:
        drain(client)
        scan_id = client.get("/api/scans").json()["scans"][0]["id"]
        latest = client.get(f"/api/scans/{scan_id}/architecture.zip")
        assert latest.status_code == 200
        revision = _contents(latest.content)[1]["scene"]["revision"]
        pinned = client.get(f"/api/scans/{scan_id}/architecture.zip", params={"revision": revision})
        assert pinned.status_code == 200
        assert pinned.content == latest.content
        assert f'r{revision}.zip' in pinned.headers["content-disposition"]
        assert client.get(f"/api/scans/{scan_id}/architecture.zip", params={"revision": 999999}).status_code == 404
        assert client.get(f"/api/scans/{scan_id}/architecture.zip", params={"revision": -1}).status_code == 404


def _full_journey_stages():
    from conftest import no_blender_stages
    from standardphysics_pipeline.discovery import DiscoveryResult

    return no_blender_stages(
        label=lambda graph, **kwargs: graph,
        discover=lambda inputs: DiscoveryResult(),
    )


def _identity() -> list[float]:
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, -0.5, 0, 1]


def _room_payload() -> bytes:
    return json.dumps({
        "version": 1,
        "story": "ground",
        "captureMetadata": {"source": "pilot"},
        "walls": [{"identifier": "11111111-1111-1111-1111-111111111111",
                   "dimensions": [4.0, 2.4, 0.2], "transform": _identity(), "confidence": "high"}],
        "floors": [{"identifier": "22222222-2222-2222-2222-222222222222",
                    "dimensions": [4.0, 0.1, 4.0], "transform": _identity(), "confidence": "high"}],
        "objects": [{"identifier": "33333333-3333-3333-3333-333333333333",
                     "category": "table", "dimensions": [1.0, 0.8, 1.0],
                     "transform": [1, 0, 0, 1.0, 0, 1, 0, 0.5, 0, 0, 1, -0.5, 0, 0, 0, 1],
                     "confidence": "medium"}],
    }).encode()


def _mesh_bytes() -> bytes:
    return json.dumps({"parts": [{
        "id": "00000000-0000-0000-0000-000000000001",
        "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        "vertices": [0, 0, 0, 1, 0, 0, 0, 1, 0],
        "triangles": [0, 1, 2],
    }]}).encode()


def test_report_assessment_and_zip_agree_on_the_pinned_scope(make_client):
    from conftest import create_scan, put_artifact

    with make_client(stages=_full_journey_stages()) as client:
        scan_id = create_scan(client)
        put_artifact(client, scan_id, "room-json", _room_payload(), "room_json")
        put_artifact(client, scan_id, "room-usdz", b"usdz", "room_usdz")
        put_artifact(client, scan_id, "frames", b"frames", "frames")
        put_artifact(client, scan_id, "poses", b"{}", "poses")
        put_artifact(client, scan_id, "lidar-mesh", _mesh_bytes(), "lidar_mesh")
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)

        # The suggestion route (R's scenario.py) 500s on this minimal room whose
        # only table sits at the room centre; confirmed and reported, not fixed
        # here. Confirm a scenario through the real endpoint instead.
        scenario = {
            "name": "Order a drink",
            "stops": [
                {"name": "Entrance", "position": {"x": 0.0, "y": -1.5, "z": 0.0}},
                {"name": "Counter", "position": {"x": 0.0, "y": 1.5, "z": 0.0}},
            ],
        }
        confirmed = client.put(f"/api/scans/{scan_id}/scenario", json=scenario)
        assert confirmed.status_code == 200, confirmed.text
        drain(client)

        assessment = client.get(f"/api/scans/{scan_id}/assessment").json()
        scope = assessment["scope"]
        assert scope is not None
        manifest_hash = scope["manifest_hash"]
        assessment_rows = {(row["requirement_id"], row["item"]["item_slug"], row["outcome"])
                           for row in scope["rows"]}

        zip_response = client.get(f"/api/scans/{scan_id}/architecture.zip")
        assert zip_response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(zip_response.content)) as zipped:
            ledger = json.loads(zipped.read("evidence-ledger.json"))
        assert ledger["assessment"]["included"] is True
        zip_scope = ledger["assessment"]["scope"]
        assert zip_scope["manifest"]["manifest_hash"] == manifest_hash
        zip_rows = {(row["requirement_id"], row["item"]["item_slug"], row["outcome"])
                    for row in zip_scope["rows"]}
        assert zip_rows == assessment_rows
        assert ledger["evidence_closure"]["evidence_manifest_hash"]

        report = client.get(f"/api/scans/{scan_id}/report").json()
        assert report["assessment"]["scope"]["manifest_hash"] == manifest_hash
        report_scene = SceneGraph.model_validate(report["scene"])
        assert graph_hash(report_scene) == ledger["scene"]["graph_hash"]
        assert report_scene.revision == ledger["scene"]["revision"]
        assert report["scenario"]["name"] == ledger["scenario"]["name"]
        assert ledger["scenario"]["pinned_version"] is not None


def test_export_unboxes_clean_of_secrets_and_filesystem_paths(make_client):
    with make_client(seed=True) as client:
        drain(client)
        scan_id = client.get("/api/scans").json()["scans"][0]["id"]
        response = client.get(f"/api/scans/{scan_id}/architecture.zip")
        assert response.status_code == 200
        forbidden = ("/Users/", "/data", ".sqlite", "Bearer ", "sp_session")
        with zipfile.ZipFile(io.BytesIO(response.content)) as zipped:
            for name in zipped.namelist():
                text = zipped.read(name).decode("utf-8", errors="replace")
                for fragment in forbidden:
                    assert fragment not in text, f"{name} leaked {fragment!r}"
