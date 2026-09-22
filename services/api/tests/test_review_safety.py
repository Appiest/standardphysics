"""Review and revision safety proofs (G04).

Owner decisions must survive reload, rediscovery and replay; a stale job or a
concurrent edit must never overwrite them silently; and a changed route,
role or evidence set must retire the assessment that is no longer current.
Every call here is an authenticated HTTP request against the real queue.
"""

import json
import uuid

from conftest import create_scan, drain, put_artifact, usdz_fixture
from standardphysics_contracts import Mat4, SceneNode, SurfaceAttachment, Vec3
from standardphysics_pipeline.discovery import DiscoveryResult


def _identity() -> list[float]:
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, -0.5, 0, 1]


def _room_payload() -> bytes:
    payload = {
        "version": 1,
        "story": "ground",
        "captureMetadata": {"source": "pilot"},
        "walls": [
            {
                "identifier": "11111111-1111-1111-1111-111111111111",
                "dimensions": [4.0, 2.4, 0.2],
                "transform": _identity(),
                "confidence": "high",
            }
        ],
        "floors": [
            {
                "identifier": "22222222-2222-2222-2222-222222222222",
                "dimensions": [4.0, 0.1, 4.0],
                "transform": _identity(),
                "confidence": "high",
            }
        ],
        "objects": [
            {
                "identifier": "33333333-3333-3333-3333-333333333333",
                "category": "table",
                "dimensions": [1.0, 0.8, 1.0],
                "transform": _identity(),
                "confidence": "medium",
            }
        ],
    }
    return json.dumps(payload).encode()


def _mesh_bytes() -> bytes:
    return json.dumps(
        {
            "parts": [
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                    "vertices": [0, 0, 0, 1, 0, 0, 0, 1, 0],
                    "triangles": [0, 1, 2],
                }
            ]
        }
    ).encode()


TABLE_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
OUTLET_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")


def _translated(x: float, y: float, z: float) -> Mat4:
    m = Mat4.identity().m
    m[12], m[13], m[14] = x, y, z
    return Mat4(m=m)


def _table_node() -> SceneNode:
    return SceneNode(
        id=TABLE_ID,
        kind="object",
        label="Table",
        raw_category="table",
        dimensions=Vec3(x=1.0, y=0.8, z=1.0),
        transform=_translated(0.7, 0.0, 0.7),
        movable=True,
        labeled_by="discovery",
    )


def _outlet_node(review_status: str = "detected") -> SceneNode:
    return SceneNode(
        id=OUTLET_ID,
        kind="outlet",
        label="Outlet",
        raw_category="outlet",
        dimensions=Vec3(x=0.12, y=0.08, z=0.03),
        transform=_translated(0.6, 0.1, 1.0),
        movable=False,
        labeled_by="discovery",
        attachment=SurfaceAttachment(review_status=review_status),
    )


def _stages(discover):
    from conftest import no_blender_stages

    return no_blender_stages(label=lambda graph, **kwargs: graph, discover=discover)


def _ready_scan(make_client, discover):
    client = make_client(stages=_stages(discover), evidence_settle_seconds=0.0)
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "room-json", _room_payload(), "room_json")
    put_artifact(client, scan_id, "room-usdz", usdz_fixture(), "room_usdz")
    put_artifact(client, scan_id, "frames", b"frame-bytes", "frames")
    put_artifact(client, scan_id, "poses", b"{}", "poses")
    put_artifact(client, scan_id, "lidar-mesh", _mesh_bytes(), "lidar_mesh")
    put_artifact(client, scan_id, "frame-0100", _jpeg(96, 64), "frames")
    put_artifact(client, scan_id, "frame-0101", _jpeg(96, 64), "frames")
    client.post(f"/api/scans/{scan_id}/complete")
    drain(client)
    return client, scan_id


def _jpeg(width: int = 96, height: int = 64) -> bytes:
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (90, 100, 110)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _two_targets(inputs):
    return DiscoveryResult(nodes=[_table_node(), _outlet_node()])


def test_all_four_target_classes_flow_through_authenticated_routes(make_client, stranger):
    client, scan_id = _ready_scan(make_client, _two_targets)
    with client:
        review = client.put(
            f"/api/scans/{scan_id}/revisions/0/outlets/{OUTLET_ID}/review",
            json={"status": "confirmed_by_user"},
        )
        assert review.status_code == 201, review.text
        assert review.json()["revision"] == 1

        television = client.put(
            f"/api/scans/{scan_id}/revisions/1/observations",
            json={"target_class": "television", "frame_id": "frame-0100", "sensor_box": [4, 4, 60, 44]},
        )
        assert television.status_code == 201, television.text
        assert television.json()["revision"] == 2

        restroom = client.put(
            f"/api/scans/{scan_id}/revisions/2/observations",
            json={"target_class": "restroom_entrance", "frame_id": "frame-0101", "sensor_box": [8, 8, 72, 52]},
        )
        assert restroom.status_code == 201, restroom.text
        assert restroom.json()["revision"] == 3

        counter = client.put(f"/api/scans/{scan_id}/revisions/3/counters/{TABLE_ID}")
        assert counter.status_code == 201, counter.text
        final = counter.json()
        assert final["revision"] == 4
        nodes = {node["id"]: node for node in final["nodes"]}
        assert nodes[str(OUTLET_ID)]["attachment"]["review_status"] == "confirmed_by_user"
        assert nodes[str(TABLE_ID)]["label"] == "service counter"
        marks = final["unlocalized_observations"]
        assert {mark["target_class"] for mark in marks} == {"television", "restroom_entrance"}
        assert all(mark["provenance"] == "manual" and mark["marked_by"] == "owner@example.com" for mark in marks)

        for revision in range(1, 5):
            reloaded = client.get(f"/api/scans/{scan_id}/scene?revision={revision}")
            assert reloaded.status_code == 200, reloaded.text

        with stranger as other:
            assert other.get(f"/api/scans/{scan_id}/scene").status_code == 404
            denied_mark = other.put(
                f"/api/scans/{scan_id}/revisions/4/observations",
                json={"target_class": "outlet", "frame_id": "f", "sensor_box": [0, 0, 1, 1]},
            )
            assert denied_mark.status_code == 404
            denied_review = other.put(
                f"/api/scans/{scan_id}/revisions/4/outlets/{OUTLET_ID}/review",
                json={"status": "rejected_by_user"},
            )
            assert denied_review.status_code == 404
            denied_counter = other.put(f"/api/scans/{scan_id}/revisions/4/counters/{TABLE_ID}")
            assert denied_counter.status_code == 404


def test_replayed_review_on_the_same_base_is_an_explicit_conflict(make_client):
    client, scan_id = _ready_scan(make_client, _two_targets)
    with client:
        first = client.put(
            f"/api/scans/{scan_id}/revisions/0/outlets/{OUTLET_ID}/review",
            json={"status": "confirmed_by_user"},
        )
        assert first.status_code == 201, first.text
        replay = client.put(
            f"/api/scans/{scan_id}/revisions/0/outlets/{OUTLET_ID}/review",
            json={"status": "confirmed_by_user"},
        )
        assert replay.status_code == 409, replay.text
        rebased = client.put(
            f"/api/scans/{scan_id}/revisions/1/outlets/{OUTLET_ID}/review",
            json={"status": "rejected_by_user"},
        )
        assert rebased.status_code == 201, rebased.text
        assert rebased.json()["revision"] == 2
        node = next(n for n in rebased.json()["nodes"] if n["id"] == str(OUTLET_ID))
        assert node["attachment"]["review_status"] == "rejected_by_user"
        reloaded = client.get(f"/api/scans/{scan_id}/scene?revision=1").json()
        kept = next(n for n in reloaded["nodes"] if n["id"] == str(OUTLET_ID))
        assert kept["attachment"]["review_status"] == "confirmed_by_user"


def test_late_process_job_replaces_ingest_revision_but_never_owner_decisions(make_client):
    def first_pass(inputs):
        return DiscoveryResult(nodes=[_table_node(), _outlet_node("detected")])

    client, scan_id = _ready_scan(make_client, first_pass)
    with client:
        review = client.put(
            f"/api/scans/{scan_id}/revisions/0/outlets/{OUTLET_ID}/review",
            json={"status": "confirmed_by_user"},
        )
        assert review.status_code == 201, review.text

    calls = {"passes": 0}

    def second_pass(inputs):
        calls["passes"] += 1
        return DiscoveryResult(nodes=[_table_node()])

    worker = client.app.state.worker
    worker.stages.discover = second_pass
    with client:
        put_artifact(client, scan_id, "frames-2", b"late evidence arrives", "frames")
        # An explicit complete is idempotent (frozen contract); with K's settle
        # gate it forces processing now, without it it is a no-op.
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)

        latest = client.get(f"/api/scans/{scan_id}/scene").json()
        assert latest["revision"] == 1
        reviewed = next(n for n in latest["nodes"] if n["id"] == str(OUTLET_ID))
        assert reviewed["attachment"]["review_status"] == "confirmed_by_user"
        base = client.get(f"/api/scans/{scan_id}/scene?revision=0").json()
        assert all(n["id"] != str(OUTLET_ID) for n in base["nodes"])
        assert calls["passes"] == 1

        assessment = client.get(f"/api/scans/{scan_id}/assessment").json()
        assert assessment["graph_revision"] == 1


def test_assessment_never_displays_a_stale_revision(make_client):
    client, scan_id = _ready_scan(make_client, _two_targets)
    with client:
        marked = client.put(
            f"/api/scans/{scan_id}/revisions/0/observations",
            json={"target_class": "outlet", "frame_id": "frame-0101", "sensor_box": [8, 8, 72, 52]},
        )
        assert marked.status_code == 201, marked.text
        stale_view = client.get(f"/api/scans/{scan_id}/assessment")
        assert stale_view.status_code == 404
        drain(client)
        current = client.get(f"/api/scans/{scan_id}/assessment")
        assert current.status_code == 200, current.text
        assert current.json()["graph_revision"] == 1


def test_changed_scenario_retires_the_old_assessment_until_rechecked(make_client):
    client, scan_id = _ready_scan(make_client, _two_targets)
    with client:
        first = client.get(f"/api/scans/{scan_id}/assessment").json()["created_at"]
        from conftest import FIXTURE_DATA

        route = json.loads((FIXTURE_DATA / "shop.scenario.json").read_text())
        confirmed = client.put(f"/api/scans/{scan_id}/scenario", json=route)
        assert confirmed.status_code == 200, confirmed.text
        retired = client.get(f"/api/scans/{scan_id}/assessment")
        assert retired.status_code == 404
        # Idempotent completion; forces the re-check now under K's settle gate.
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)
        fresh = client.get(f"/api/scans/{scan_id}/assessment").json()
        assert fresh["created_at"] != first
