"""Open audit findings, pinned as strict expected failures.

Each test states what correct behaviour looks like and fails on today's code,
so CI stays green while the bug exists. `strict=True` turns a fix into a
failing XPASS, so whoever fixes a finding deletes its marker in the same push
and the finding closes. Reproductions are in PROGRESS.md. The lane that owns
the code fixes it; the audit only pins it.
"""

import contextlib
import hashlib
import json
import uuid
from pathlib import Path

import pytest
import standardphysics_fixtures
from fastapi.testclient import TestClient
from standardphysics_agents import VerificationLedger
from standardphysics_contracts import (
    Mat4,
    SaveLayoutRequest,
    Scenario,
    SceneGraph,
    SceneNode,
    Stop,
    Vec3,
    to_meters,
)
from standardphysics_fixtures import build_graph, build_scenario, node_id
from standardphysics_pipeline import blender
from standardphysics_pipeline.ingest import parse_room_json
from standardphysics_pipeline.measure import PipelineMeasurements
from standardphysics_pipeline.occupancy import blocks_floor

from standardphysics_api import layout
from standardphysics_api.app import create_app
from standardphysics_api.settings import Settings
from standardphysics_api.stages import Stages

THICKNESS = 0.1
DEPTH = 4.0
REAL_EXPORTS = Path(standardphysics_fixtures.__file__).parent / "data" / "real"
DATASET_PHONE = Path(__file__).resolve().parents[1] / "datasets" / "phone"


def _box(name: str, kind: str, centre, dims, movable: bool = False) -> SceneNode:
    return SceneNode(
        id=uuid.uuid5(uuid.NAMESPACE_OID, f"audit-open-{name}"),
        kind=kind,
        label=name,
        raw_category=kind,
        dimensions=Vec3(x=dims[0], y=dims[1], z=dims[2]),
        transform=Mat4.translation(*centre),
        movable=movable,
    )


def _turn_room(lane_inches: float, tip_gap_inches: float):
    """Two lanes split by a thin partition; the only route turns 180 degrees
    around its tip. Every gap is set exactly, so the widths have known answers."""
    lane, gap = to_meters(lane_inches), to_meters(tip_gap_inches)
    inner_width = 2 * lane + THICKNESS
    tip_y = DEPTH / 2 - THICKNESS / 2 - gap
    length = tip_y + DEPTH / 2
    outer = inner_width + 2 * THICKNESS
    nodes = [
        _box("south", "wall", (0, -DEPTH / 2, 1.5), (outer, THICKNESS, 3)),
        _box("north", "wall", (0, DEPTH / 2, 1.5), (outer, THICKNESS, 3)),
        _box("west", "wall", (-inner_width / 2 - THICKNESS / 2, 0, 1.5), (THICKNESS, DEPTH, 3)),
        _box("east", "wall", (inner_width / 2 + THICKNESS / 2, 0, 1.5), (THICKNESS, DEPTH, 3)),
        _box("partition", "object", (0, -DEPTH / 2 + length / 2, 1.0), (THICKNESS, length, 2.0)),
    ]
    lane_centre = THICKNESS / 2 + lane / 2
    scenario = Scenario(
        stops=[
            Stop(name="West lane", position=Vec3(x=-lane_centre, y=-1.5, z=0.0)),
            Stop(name="East lane", position=Vec3(x=lane_centre, y=-1.5, z=0.0)),
        ]
    )
    return SceneGraph(scan_id=uuid.uuid4(), nodes=nodes), scenario


@pytest.mark.xfail(strict=True, reason="A-14: turn widths measure the pivot's corner, not the gap")
def test_a14_a_compliant_turn_measures_its_real_gaps():
    graph, scenario = _turn_room(lane_inches=43, tip_gap_inches=49)
    turn = PipelineMeasurements().turn_detail(graph, scenario, 0)
    widths = (turn.approach_inches, turn.at_turn_inches, turn.leaving_inches)
    assert widths == pytest.approx((43.0, 49.0, 43.0), abs=0.5)


@pytest.mark.xfail(strict=True, reason="A-15: a turn over 60 in is undermeasured, so the exemption cannot apply")
def test_a15_a_turn_over_sixty_inches_measures_over_sixty():
    graph, scenario = _turn_room(lane_inches=36, tip_gap_inches=61)
    turn = PipelineMeasurements().turn_detail(graph, scenario, 0)
    assert turn.at_turn_inches == pytest.approx(61.0, abs=0.5)


@pytest.mark.xfail(strict=True, reason="A-6: everything near a stop is ignored, not only its anchor")
def test_a6_an_obstruction_just_inside_the_entrance_narrows_the_route():
    graph, scenario = build_graph(), build_scenario()
    entrance = scenario.stops[0].position
    width = 2.5
    offset = to_meters(20) / 2 + width / 2
    for x in (entrance.x - offset, entrance.x + offset):
        graph.nodes.append(
            _box(f"sign-{x:.2f}", "object", (x, entrance.y + 0.2, 0.6), (width, 0.08, 1.2), movable=True)
        )
    result = PipelineMeasurements().route_clear_width(graph, scenario, 0)
    assert result.inches == pytest.approx(20.0, abs=0.5)


def test_a9_door_clear_width_asks_for_a_measurement():
    result = PipelineMeasurements().door_clear_width(build_graph(), node_id("door_front"))
    assert result.needs_measurement


def test_a22_leg_one_is_not_the_gap_between_the_counter_and_a_table():
    result = PipelineMeasurements().route_clear_width(build_graph(), build_scenario(), 1)
    assert result.inches > 36.0


def _without_blender(*args, **kwargs):
    raise blender.BlenderError("the audit tests do not launch Blender")


def _upload(client: TestClient, scan_id: str, artifact_id: str, body: bytes, kind: str) -> None:
    client.put(
        f"/api/scans/{scan_id}/artifacts/{artifact_id}",
        content=body,
        headers={"X-Checksum-SHA256": hashlib.sha256(body).hexdigest(), "X-Artifact-Kind": kind},
    )


def _finalize_and_process(client: TestClient, scan_id: str) -> str:
    client.post(f"/api/scans/{scan_id}/complete")
    client.app.state.worker.drain()
    return client.get(f"/api/scans/{scan_id}").json()["state"]


AUDIT_PASSWORD = "audit-owner-password"


@contextlib.contextmanager
def _api_client(tmp_path, seed_sample_shop: bool = False):
    """A running API with an owner already signed in.

    The sample shop belongs to the seeded demo account, so a seeded client
    signs in as that owner rather than registering a second one who would see
    an empty list.
    """
    stages = Stages(
        ledger_factory=VerificationLedger,
        export_glb=_without_blender, usdz_to_glb=_without_blender, render_finding=_without_blender,
    )
    settings = Settings(
        data_dir=tmp_path / "var", seed_sample_shop=seed_sample_shop, seed_owner_password=AUDIT_PASSWORD
    )
    with TestClient(create_app(settings, stages, run_worker=False)) as client:
        _sign_in(client, settings, seed_sample_shop)
        yield client


def _sign_in(client: TestClient, settings: Settings, seeded: bool) -> None:
    if seeded:
        credentials = {"email": settings.seed_owner_email, "password": AUDIT_PASSWORD}
        assert client.post("/api/auth/sign-in", json=credentials).status_code == 200
        return
    registration = {"email": "audit@example.com", "password": AUDIT_PASSWORD, "shop_name": "Audit"}
    assert client.post("/api/auth/sign-up", json=registration).status_code == 201


def _sample_shop_id(client: TestClient) -> str:
    return client.get("/api/scans").json()["scans"][0]["id"]


def _slide(name: str, dx: float) -> dict:
    return {"node_id": str(node_id(name)), "delta_translation": {"x": dx, "y": 0.0, "z": 0.0}, "delta_rotation_z_degrees": 0.0}


def _sealed_aisle_graph() -> SceneGraph:
    graph = build_graph()
    case = graph.by_id(node_id("case_east"))
    case.dimensions.x = 6.0
    case.transform.m[3] = 0.0
    return graph


def test_a40_every_object_named_for_a_sealed_route_would_reopen_it():
    scenario = build_scenario()
    named = PipelineMeasurements().route_clear_width(_sealed_aisle_graph(), scenario, 0).blocking_node_ids
    assert named
    for blocker in named:
        graph = _sealed_aisle_graph()
        graph.nodes = [node for node in graph.nodes if node.id != blocker]
        assert PipelineMeasurements().route_clear_width(graph, scenario, 0).reachable


def test_a49_a_real_scan_that_is_ready_has_been_checked(tmp_path):
    with _api_client(tmp_path) as client:
        body = {"name": "Corner cafe", "device_model": "iPhone17,1", "duration_seconds": 60.0}
        scan_id = client.post("/api/scans", json=body).json()["id"]
        room = (REAL_EXPORTS / "apple_bedroom3.room.json").read_bytes()
        _upload(client, scan_id, "room-json", room, "room_json")
        _upload(client, scan_id, "room-usdz", (DATASET_PHONE / "test1" / "room.usdz").read_bytes(), "room_usdz")
        assert _finalize_and_process(client, scan_id) == "ready"
        assert client.get(f"/api/scans/{scan_id}/scene").json()["nodes"]
        assert client.get(f"/api/scans/{scan_id}/assessment").status_code == 200


def test_a29_a_failed_scan_is_processed_again_once_a_readable_room_arrives(tmp_path):
    with _api_client(tmp_path) as client:
        body = {"name": "Corner cafe", "device_model": "iPhone17,1", "duration_seconds": 60.0}
        scan_id = client.post("/api/scans", json=body).json()["id"]
        _upload(client, scan_id, "room-json", b"{not json", "room_json")
        _upload(client, scan_id, "room-usdz", (DATASET_PHONE / "test1" / "room.usdz").read_bytes(), "room_usdz")
        assert _finalize_and_process(client, scan_id) == "failed"
        readable = (REAL_EXPORTS / "apple_bedroom3.room.json").read_bytes()
        _upload(client, scan_id, "room-json-2", readable, "room_json")
        assert _finalize_and_process(client, scan_id) == "ready"


def test_a31_a_render_comes_from_the_newest_revision(tmp_path):
    with _api_client(tmp_path, seed_sample_shop=True) as client:
        scan_id = _sample_shop_id(client)
        finding_id = uuid.uuid4()
        revisions = client.app.state.store.scan_dir(uuid.UUID(scan_id)) / "revisions"
        for revision in (9, 10):
            png = revisions / str(revision) / "renders" / f"{finding_id}.png"
            png.parent.mkdir(parents=True)
            png.write_bytes(f"revision {revision}".encode())
        assert client.get(f"/api/scans/{scan_id}/renders/{finding_id}.png").content == b"revision 10"


def test_a35_a_save_that_loses_the_race_is_refused(tmp_path, monkeypatch):
    with _api_client(tmp_path, seed_sample_shop=True) as client:
        scan_id = _sample_shop_id(client)
        checked = layout._candidate

        def another_save_lands_first(base, moves):
            monkeypatch.setattr(layout, "_candidate", checked)
            other = SaveLayoutRequest.model_validate({"base_revision": 0, "moves": [_slide("case_west", -0.05)]})
            layout.save_layout(client.app.state.database, client.app.state.worker, uuid.UUID(scan_id), other)
            return checked(base, moves)

        monkeypatch.setattr(layout, "_candidate", another_save_lands_first)
        mine = {"base_revision": 0, "moves": [_slide("case_east", 0.127)]}
        assert client.post(f"/api/scans/{scan_id}/revisions", json=mine).status_code == 409


def _assess_that_raises(*args, **kwargs):
    raise RuntimeError("the audit's broken assess stage")


def test_a43_a_retried_scan_whose_assessment_still_fails_ends_failed(tmp_path):
    with _api_client(tmp_path, seed_sample_shop=True) as client:
        client.app.state.worker.stages.assess = _assess_that_raises
        scan_id = _sample_shop_id(client)
        client.app.state.worker.drain()
        assert client.get(f"/api/scans/{scan_id}").json()["state"] == "failed"
        client.post(f"/api/scans/{scan_id}/complete")
        client.app.state.worker.drain()
        assert client.get(f"/api/scans/{scan_id}").json()["state"] == "failed"


@pytest.mark.parametrize("room", ["apple_bedroom3", "apple_livingroom"])
def test_a25_furniture_in_a_real_export_blocks_the_floor(room):
    """Fixed in 5d09e4d, which stands the room on its floor during ingest."""
    export = json.loads((REAL_EXPORTS / f"{room}.room.json").read_text())
    graph = parse_room_json(export)
    furniture = [
        node for node in graph.nodes
        if node.kind == "object" and node.raw_category in {"bed", "table", "chair", "sofa"}
    ]
    assert furniture
    assert all(blocks_floor(node) for node in furniture)
