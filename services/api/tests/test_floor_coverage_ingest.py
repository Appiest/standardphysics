"""The floor coverage map is measured at ingest and reaches every layout that is rearranged."""

import hashlib
import json
import uuid

from standardphysics_agents.fix import NO_FLOOR_MAP
from standardphysics_pipeline.discovery import DiscoveryResult
from standardphysics_pipeline.floor_coverage import ObservedFloor

from conftest import create_scan, drain, no_blender_stages, put_artifact, usdz_fixture
from standardphysics_api import repository as repo
from standardphysics_api.rearrangement_base import rearrangement_base
from standardphysics_api.simulations import _rearranging_limitations

FLOOR_Y = -0.5
"""The floor's ARKit height; ingest lowers it to z = 0."""


def _placed_at_floor() -> list[float]:
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, FLOOR_Y, 0, 1]


def _room_payload() -> bytes:
    def surface(identifier, dimensions, **extra):
        return {"identifier": identifier, "dimensions": dimensions, "transform": _placed_at_floor(),
                "confidence": "high", **extra}

    return json.dumps({
        "version": 1,
        "story": "ground",
        "captureMetadata": {"source": "pilot"},
        "walls": [surface("11111111-1111-1111-1111-111111111111", [4.0, 2.4, 0.2])],
        "floors": [surface("22222222-2222-2222-2222-222222222222", [4.0, 0.01, 4.0])],
        "objects": [surface("33333333-3333-3333-3333-333333333333", [0.6, 0.8, 0.6], category="storage")],
    }).encode()


def _floor_mesh() -> bytes:
    """One square metre of floor seen around the middle of the room, as the phone writes it."""
    vertices = [-1.0, FLOOR_Y, -1.0, 1.0, FLOOR_Y, -1.0, 1.0, FLOOR_Y, 1.0, -1.0, FLOOR_Y, 1.0]
    return json.dumps({"parts": [{
        "id": "00000000-0000-0000-0000-000000000001",
        "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        "vertices": vertices,
        "triangles": [0, 1, 2, 0, 2, 3],
    }]}).encode()


def _ingested(make_client, mesh: bytes | None):
    stages = no_blender_stages(label=lambda graph, **kwargs: graph, discover=lambda inputs: DiscoveryResult())
    client = make_client(stages=stages, evidence_settle_seconds=0.0).__enter__()
    scan_id = create_scan(client)
    if mesh is not None:
        put_artifact(client, scan_id, "lidar-mesh", mesh, "lidar_mesh")
    put_artifact(client, scan_id, "room-json", _room_payload(), "room_json")
    put_artifact(client, scan_id, "room-usdz", usdz_fixture(), "room_usdz")
    assert client.post(f"/api/scans/{scan_id}/complete").status_code == 200
    drain(client)
    with client.app.state.database.connect() as connection:
        graph = repo.graph_of(repo.get_revision(connection, uuid.UUID(scan_id), 0))
    return client, scan_id, graph


def _piece_at(graph, x: float, y: float):
    piece = next(node for node in graph.nodes if node.kind == "object")
    return piece.model_copy(update={"transform": piece.transform.model_copy(update={
        "m": [*piece.transform.m[:3], x, *piece.transform.m[4:7], y, *piece.transform.m[8:]],
    })})


def test_ingest_measures_the_floor_the_mesh_saw_and_names_the_mesh(make_client):
    mesh = _floor_mesh()
    _, _, graph = _ingested(make_client, mesh)
    (coverage,) = graph.floor_coverage
    assert coverage.mesh_sha256 == hashlib.sha256(mesh).hexdigest()
    seen = ObservedFloor.of(graph)
    assert seen.unseen_share(_piece_at(graph, 0.5, 0.5)) == 0.0
    assert seen.unseen_share(_piece_at(graph, 1.6, 1.6)) == 1.0


def test_a_scan_without_a_mesh_has_no_map_and_a_simulation_says_so(make_client):
    _, _, graph = _ingested(make_client, None)
    assert graph.floor_coverage == []
    assert _rearranging_limitations(graph, None) == [NO_FLOOR_MAP]


def test_a_revision_saved_before_coverage_was_measured_gets_it_from_the_scan(make_client):
    client, scan_id, graph = _ingested(make_client, _floor_mesh())
    older = graph.model_copy(update={"revision": 1, "floor_coverage": []})
    with client.app.state.database.transaction() as connection:
        repo.save_revision(connection, older, source="owner", base_revision=0)
    with client.app.state.database.connect() as connection:
        base = rearrangement_base(connection, repo.get_revision(connection, uuid.UUID(scan_id), 1))
    assert base.floor_coverage == graph.floor_coverage
    assert _rearranging_limitations(base, None) == []
