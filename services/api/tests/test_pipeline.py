"""A real RoomPlan export and the sample shop, through every stage."""

import json
import uuid

from standardphysics_pipeline import glb_node_names, reconstruct

from conftest import FIXTURE_DATA, create_scan, drain, put_artifact
from standardphysics_api.stages import Stages

REAL = FIXTURE_DATA / "real"


def test_default_ingest_uses_local_reconstruction_without_a_network_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    room_json = tmp_path / "room.json"
    room_json.write_text(json.dumps({
        "objects": [{
            "identifier": str(uuid.uuid4()), "category": "chair", "confidence": "high",
            "dimensions": [0.5, 0.9, 0.5],
            "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        }],
    }))

    stages = Stages()
    graph = stages.ingest(room_json, uuid.uuid4())

    assert stages.label is reconstruct
    assert graph.nodes[0].label == "Chair"
    assert graph.nodes[0].labeled_by == "roomplan"


def test_geometry_exports_the_object_graph_before_considering_a_scan(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    room_json = tmp_path / "room.json"
    room_json.write_text(json.dumps({
        "objects": [{
            "identifier": str(uuid.uuid4()), "category": "chair", "confidence": "high",
            "dimensions": [0.5, 0.9, 0.5],
            "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        }],
    }))
    graph = Stages().ingest(room_json, uuid.uuid4())
    out = tmp_path / "scene.glb"
    calls = []

    def export_graph(actual_graph, actual_out):
        calls.append((actual_graph, actual_out))
        return actual_out

    def scanned_mesh(*_):
        raise AssertionError("the scan must not replace available graph geometry")

    stage = Stages(export_glb=export_graph, usdz_to_glb=scanned_mesh)
    assert stage.geometry(graph, out, tmp_path / "room.usdz", tmp_path / "mapping.json") == out
    assert calls == [(graph, out)]


def _upload_real_room(client, room: str) -> str:
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "room-json", (REAL / f"{room}.room.json").read_bytes(), "room_json")
    put_artifact(client, scan_id, "room-usdz", (REAL / f"{room}.usdz").read_bytes(), "room_usdz")
    put_artifact(client, scan_id, "room-metadata", (REAL / f"{room}.metadata.plist").read_bytes(), "room_metadata")
    client.post(f"/api/scans/{scan_id}/complete")
    drain(client)
    return scan_id


def test_a_real_export_becomes_a_ready_scan_with_a_scene(client):
    scan_id = _upload_real_room(client, "apple_livingroom")
    assert client.get(f"/api/scans/{scan_id}").json()["state"] == "ready"
    scene = client.get(f"/api/scans/{scan_id}/scene").json()
    room = json.loads((REAL / "apple_livingroom.room.json").read_text())
    expected = sum(len(room.get(key) or []) for key in ("walls", "doors", "windows", "openings", "floors", "objects"))
    assert len(scene["nodes"]) == expected
    assert scene["scan_id"] == scan_id


def test_display_geometry_falls_back_to_the_graph_when_conversion_fails(client):
    scan_id = _upload_real_room(client, "apple_bedroom3")
    response = client.get(f"/api/scans/{scan_id}/scene.glb")
    assert response.status_code == 200
    assert response.content[:4] == b"glTF"
    metadata = client.head(f"/api/scans/{scan_id}/scene.glb")
    assert metadata.status_code == 200
    assert metadata.content == b""
    assert metadata.headers["X-Exported-Revision"] == response.headers["X-Exported-Revision"]


def test_a_real_scan_without_stops_is_assessed_without_its_route(client):
    scan_id = _upload_real_room(client, "apple_bedroom3")
    assert client.get(f"/api/scans/{scan_id}/scenario").status_code == 404
    assert client.get(f"/api/scans/{scan_id}/assessment").json()["graph_revision"] == 0


def test_rebuild_geometry_is_revision_pinned_and_reports_pending_work(client):
    scan_id = _upload_real_room(client, "apple_bedroom3")
    url = f"/api/scans/{scan_id}/scene.glb"
    assert client.head(url).headers["X-Display-Pending"] == "false"
    assert client.post(f"/api/scans/{scan_id}/rebuild", json={"base_revision": 0}).status_code == 201
    waiting = client.head(url + "?revision=1")
    assert waiting.status_code == 404
    assert waiting.headers["X-Display-Pending"] == "true"
    drain(client)
    assert client.get(url).headers["X-Exported-Revision"] == "1"
    assert client.get(url + "?revision=0").headers["X-Exported-Revision"] == "0"
    assert client.head(url + "?revision=1").headers["X-Display-Pending"] == "false"
    assert client.get(url + "?revision=99").status_code == 404


def test_the_sample_shop_is_ready_with_its_findings(make_client):
    with make_client(seed=True) as client:
        drain(client)
        scans = client.get("/api/scans").json()["scans"]
        assert [s["name"] for s in scans] == ["Sample boba shop"]
        scan_id = scans[0]["id"]
        assert scans[0]["state"] == "ready"
        assessment = client.get(f"/api/scans/{scan_id}/assessment").json()
        widths = {round(f["measured_inches"]) for f in assessment["findings"] if f["outcome"] == "problem" and f["measured_inches"]}
        assert 31 in widths
        assert client.get(f"/api/scans/{scan_id}/scenario").json()["stops"][0]["name"] == "Entrance"
        assert client.get(f"/api/scans/{scan_id}/scene.glb").content[:4] == b"glTF"


def test_the_sample_shop_is_the_lawsuit_counter(make_client, tmp_path):
    with make_client(seed=True) as client:
        drain(client)
        scan_id = client.get("/api/scans").json()["scans"][0]["id"]
        scene = client.get(f"/api/scans/{scan_id}/scene").json()
        assert {"Lowered counter section", "Card reader"} <= {node["label"] for node in scene["nodes"]}
        glb = tmp_path / "scene.glb"
        glb.write_bytes(client.get(f"/api/scans/{scan_id}/scene.glb").content)
        assert {node["id"] for node in scene["nodes"]} <= set(glb_node_names(glb))
        findings = client.get(f"/api/scans/{scan_id}/assessment").json()["findings"]
        assert any(f["outcome"] == "problem" and "card reader" in (f["fix"] or "").lower() for f in findings)


def test_findings_with_a_locus_get_a_render(make_client):
    with make_client(seed=True) as client:
        drain(client)
        scan_id = client.get("/api/scans").json()["scans"][0]["id"]
        finding = next(f for f in client.get(f"/api/scans/{scan_id}/assessment").json()["findings"] if f["locus"])
        render_url = finding["locus"]["render_url"]
        assert render_url == f"/api/scans/{scan_id}/renders/{finding['id']}.png"
        assert client.get(render_url).content.startswith(b"\x89PNG")


def test_seeding_twice_keeps_one_sample_shop(make_client):
    with make_client(seed=True) as client:
        drain(client)
    with make_client(seed=True) as client:
        assert len(client.get("/api/scans").json()["scans"]) == 1


def test_without_verified_rules_the_sample_shop_reports_nothing(make_client):
    from standardphysics_agents import VerificationLedger

    from conftest import no_blender_stages

    with make_client(seed=True, stages=no_blender_stages(ledger_factory=VerificationLedger)) as client:
        drain(client)
        scan_id = client.get("/api/scans").json()["scans"][0]["id"]
        assert client.get(f"/api/scans/{scan_id}/assessment").json()["findings"] == []


def test_an_assessment_says_how_many_rules_ran(make_client):
    from standardphysics_agents import VerificationLedger

    from conftest import no_blender_stages

    with make_client(seed=True, stages=no_blender_stages(ledger_factory=VerificationLedger)) as client:
        drain(client)
        scan_id = client.get("/api/scans").json()["scans"][0]["id"]
        assert client.get(f"/api/scans/{scan_id}/assessment").json()["rules_checked"] == 0
