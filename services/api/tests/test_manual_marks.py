"""Manual photo marking contract, frozen by K (coordinator plan 7c).

A person's mark attaches to a measured node as a manual observation, or to the
graph as an unlocalized observation when no reliable surface exists. Manual
never becomes automatic, and the actor rides with the mark. B owns persistence
hardening; U consumes these exact shapes.
"""

import json

from conftest import create_scan, drain, put_artifact, usdz_fixture, usdz_fixture


def _stages():
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
        "walls": [{"identifier": "11111111-1111-1111-1111-111111111111", "dimensions": [4.0, 2.4, 0.2],
                   "transform": _identity(), "confidence": "high"}],
        "floors": [{"identifier": "22222222-2222-2222-2222-222222222222", "dimensions": [4.0, 0.1, 4.0],
                    "transform": _identity(), "confidence": "high"}],
        "objects": [{"identifier": "33333333-3333-3333-3333-333333333333", "category": "table",
                     "dimensions": [1.0, 0.8, 1.0], "transform": _identity(),
                     "confidence": "medium"}],
    }).encode()


def _mesh_bytes() -> bytes:
    return json.dumps({"parts": [{
        "id": "00000000-0000-0000-0000-000000000001",
        "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        "vertices": [0, 0, 0, 1, 0, 0, 0, 1, 0],
        "triangles": [0, 1, 2],
    }]}).encode()


def _jpeg(width: int = 64, height: int = 48) -> bytes:
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 130, 140)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _ready_scan(make_client):
    stages = _stages()
    client = make_client(stages=stages)
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "room-json", _room_payload(), "room_json")
    put_artifact(client, scan_id, "room-usdz", usdz_fixture(), "room_usdz")
    put_artifact(client, scan_id, "poses", b"{}", "poses")
    put_artifact(client, scan_id, "lidar-mesh", _mesh_bytes(), "lidar_mesh")
    put_artifact(client, scan_id, "frame-0000", _jpeg(), "frames")
    client.post(f"/api/scans/{scan_id}/complete")
    drain(client)
    return client, scan_id


def test_manual_mark_on_a_node_is_manual_and_persists(make_client):
    client, scan_id = _ready_scan(make_client)
    with client:
        nodes = client.get(f"/api/scans/{scan_id}/scene").json()["nodes"]
        table = next(node for node in nodes if node["kind"] == "object")
        response = client.put(
            f"/api/scans/{scan_id}/revisions/0/observations",
            json={
                "target_class": "outlet",
                "node_id": table["id"],
                "frame_id": "frame-0000",
                "sensor_box": [4, 4, 40, 30],
                "note": "owner spotted this",
            },
        )
        assert response.status_code == 201, response.text
        saved = response.json()
        assert saved["revision"] == 1
        marked = next(node for node in saved["nodes"] if node["id"] == table["id"])
        observations = marked["attachment"]["observations"]
        assert len(observations) == 1
        assert observations[0]["provenance"] == "manual"
        assert observations[0]["marked_by"] == "owner@example.com"
        assert observations[0]["note"] == "owner spotted this"

        reloaded_response = client.get(f"/api/scans/{scan_id}/scene?revision=1")
        reloaded = reloaded_response.json()["nodes"]
        persisted = next(node for node in reloaded if node["id"] == table["id"])
        assert persisted["attachment"]["observations"][0]["provenance"] == "manual"


def test_mark_without_a_node_stays_unlocalized_and_authentication_holds(make_client, stranger):
    client, scan_id = _ready_scan(make_client)
    with client:
        response = client.put(
            f"/api/scans/{scan_id}/revisions/0/observations",
            json={
                "target_class": "television",
                "frame_id": "frame-0000",
                "sensor_box": [8, 8, 44, 36],
            },
        )
        assert response.status_code == 201, response.text
        saved = response.json()
        assert saved["revision"] == 1
        marks = saved["unlocalized_observations"]
        assert len(marks) == 1
        assert marks[0]["target_class"] == "television"
        assert marks[0]["provenance"] == "manual"
        assert "position" not in marks[0] and "transform" not in marks[0]

        owner_view = client.get(f"/api/scans/{scan_id}/scene").json()
        assert owner_view["unlocalized_observations"][0]["marked_by"] == "owner@example.com"

        with stranger as other:
            assert other.get(f"/api/scans/{scan_id}/scene").status_code == 404
            denied = other.put(
                f"/api/scans/{scan_id}/revisions/1/observations",
                json={"target_class": "outlet", "frame_id": "f", "sensor_box": [0, 0, 1, 1]},
            )
            assert denied.status_code == 404


def test_marking_a_missing_node_is_a_clear_404(make_client):
    client, scan_id = _ready_scan(make_client)
    with client:
        response = client.put(
            f"/api/scans/{scan_id}/revisions/0/observations",
            json={
                "target_class": "outlet",
                "node_id": "11111111-2222-3333-4444-555555555555",
                "frame_id": "frame-0000",
                "sensor_box": [0, 0, 10, 10],
            },
        )
        assert response.status_code == 404


def test_conflicting_concurrent_revision_is_explicit(make_client):
    client, scan_id = _ready_scan(make_client)
    with client:
        first = client.put(
            f"/api/scans/{scan_id}/revisions/0/observations",
            json={"target_class": "outlet", "frame_id": "frame-0000", "sensor_box": [2, 2, 20, 16]},
        )
        assert first.status_code == 201, first.text
        stale = client.put(
            f"/api/scans/{scan_id}/revisions/0/observations",
            json={"target_class": "outlet", "frame_id": "frame-0000", "sensor_box": [2, 2, 20, 16]},
        )
        assert stale.status_code == 409
