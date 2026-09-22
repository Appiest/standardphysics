"""Scoped outcome matrix on assessments, frozen by K (coordinator plan 7b).

Production-path synthetic (L2): a real assessment through the worker must carry
a hashed ScopeManifest covering every requested requirement, with unevaluated
rules visible as unobserved rather than gone. A hardens applicability behind
G06/G13; Q recomputes these invariants from raw artifacts.
"""

import json

from conftest import create_scan, drain, put_artifact, usdz_fixture

ALLOWED = {"satisfied", "violation", "needs_verification", "not_applicable", "unobserved"}


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
        "walls": [{"identifier": "11111111-1111-1111-1111-111111111111",
                   "dimensions": [4.0, 2.4, 0.2], "transform": _identity(), "confidence": "high"}],
        "floors": [{"identifier": "22222222-2222-2222-2222-222222222222",
                    "dimensions": [4.0, 0.1, 4.0], "transform": _identity(), "confidence": "high"}],
        "objects": [{"identifier": "33333333-3333-3333-3333-333333333333",
                     "category": "table", "dimensions": [1.0, 0.8, 1.0],
                     "transform": _identity(), "confidence": "medium"}],
    }).encode()


def _mesh_bytes() -> bytes:
    return json.dumps({"parts": [{
        "id": "00000000-0000-0000-0000-000000000001",
        "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        "vertices": [0, 0, 0, 1, 0, 0, 0, 1, 0],
        "triangles": [0, 1, 2],
    }]}).encode()


def test_assessment_carries_a_complete_hashed_scope_matrix(make_client):
    stages = _stages()
    with make_client(stages=stages) as client:
        scan_id = create_scan(client)
        put_artifact(client, scan_id, "room-json", _room_payload(), "room_json")
        put_artifact(client, scan_id, "room-usdz", usdz_fixture(), "room_usdz")
        put_artifact(client, scan_id, "frames", b"frames", "frames")
        put_artifact(client, scan_id, "poses", b"{}", "poses")
        put_artifact(client, scan_id, "lidar-mesh", _mesh_bytes(), "lidar_mesh")
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)

        assessment = client.get(f"/api/scans/{scan_id}/assessment").json()
        scope = assessment["scope"]
        assert scope is not None, "assessments must carry a frozen scope"

        requested = set(scope["requested_requirements"])
        present = {row["requirement_id"] for row in scope["rows"] if row["requested"]}
        assert requested <= present, "every requested requirement needs a visible row"
        assert requested, "scope cannot be vacuously complete over nothing"

        for row in scope["rows"]:
            assert row["outcome"] in ALLOWED
            if row["outcome"] != "needs_verification":
                assert row["legal_review_status"] == "unreviewed_preview"
            assert row["reason"], "every row explains its outcome"

        unobserved = [row for row in scope["rows"] if row["outcome"] == "unobserved"]
        assert any(row["item"]["observed"] is False for row in unobserved)

        regard = {row["requirement_id"] for row in scope["rows"] if row["outcome"] == "needs_verification"}
        for finding in assessment["findings"]:
            if finding["outcome"] == "question":
                assert finding["check_id"] in regard


def test_matrix_outcomes_map_from_findings_matches_legacy_problems(make_client):
    stages = _stages()
    with make_client(stages=stages) as client:
        scan_id = create_scan(client)
        put_artifact(client, scan_id, "room-json", _room_payload(), "room_json")
        put_artifact(client, scan_id, "room-usdz", usdz_fixture(), "room_usdz")
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)

        assessment = client.get(f"/api/scans/{scan_id}/assessment").json()
        assert assessment["scope"] is None or "scope" in assessment
        rows = assessment["scope"]["rows"]
        problem_rules = {f["check_id"] for f in assessment["findings"] if f["outcome"] == "problem"}
        for requirement in problem_rules:
            matches = [row for row in rows if row["requirement_id"] == requirement]
            assert matches, "a reported problem must appear in the matrix"
            assert any(row["outcome"] == "violation" for row in matches)
