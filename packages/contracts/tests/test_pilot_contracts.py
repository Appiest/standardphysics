"""Pilot contract freeze tests (K).

These pin the three Wave-0 producer/consumer examples at the schema layer:
evidence closure status, the scoped outcome matrix on Assessment, and manual
photo marks with unlocalized support. They are contract tests, not evidence of
any detector or measurement having run.
"""

import pytest
from pydantic import ValidationError

from standardphysics_contracts import (
    Assessment,
    EvidenceBundle,
    EvidenceStatus,
    ManualMarkRequest,
    ObservationCrop,
    ScopeItem,
    ScopeManifest,
    ScopeRow,
    SceneGraph,
    UnlocalizedObservation,
)


def assessment(**overrides):
    base = dict(
        id="00000000-0000-0000-0000-000000000001",
        scan_id="00000000-0000-0000-0000-000000000002",
        graph_revision=0,
        graph_hash="abc",
        rulepack_version="test",
        pass_number=1,
        created_at="2026-09-21T00:00:00Z",
        findings=[],
    )
    return Assessment(**{**base, **overrides})


def row(slug, requirement, outcome="unobserved"):
    return ScopeRow(
        item=ScopeItem(item_slug=slug, item_kind="site", label=requirement),
        requirement_id=requirement,
        outcome=outcome,
    )


def manifest(required, rows):
    return ScopeManifest(
        id="00000000-0000-0000-0000-000000000003",
        scan_id="00000000-0000-0000-0000-000000000002",
        version=1,
        created_at="2026-09-21T00:00:00Z",
        graph_revision=0,
        graph_hash="abc",
        rulepack_version="test",
        manifest_hash="h",
        requested_requirements=required,
        rows=rows,
    )


def test_legacy_assessment_without_scope_still_round_trips():
    a = assessment()
    dumped = a.model_dump(mode="json")
    assert dumped["scope"] is None
    assert Assessment.model_validate(dumped) == a
    assert a.outcome_coverage_complete is False


def test_assessment_matrix_coverage_is_about_requested_requirements():
    a = assessment(
        scope=manifest(
            required=["a", "b"],
            rows=[row("site:a", "a"), row("site:b", "b")],
        )
    )
    assert a.outcome_coverage_complete is True
    missing = assessment(scope=manifest(required=["a", "b", "c"], rows=[row("site:a", "a")]))
    assert missing.outcome_coverage_complete is False


def test_outcome_enum_rejects_invented_states():
    with pytest.raises(ValidationError):
        row("x", "r", outcome="completely_fine")


def test_evidence_status_rejects_bad_semantic_state():
    with pytest.raises(ValidationError):
        EvidenceStatus(
            scan_id="00000000-0000-0000-0000-000000000002",
            geometry_state="ready",
            evidence_state="complete",
            semantic_state="party_time",
            complete_evidence=True,
        )


def test_evidence_bundle_versioned_closure_round_trip():
    bundle = EvidenceBundle(
        version=2,
        manifest_hash="m",
        artifact_hashes={"frames": "f", "poses": "p", "lidar_mesh": "l"},
        complete=True,
        semantic_processed_hash=None,
    )
    again = EvidenceBundle.model_validate(bundle.model_dump(mode="json"))
    assert again == bundle
    assert again.semantic_processed_hash is None
    assert again.complete is True


def test_observation_crops_are_automatic_unless_marked_manual():
    automatic = ObservationCrop(frame_id="f", sensor_box=[0, 0, 10, 10])
    assert automatic.provenance == "automatic"
    manual = ObservationCrop(frame_id="f", sensor_box=[0, 0, 10, 10], provenance="manual")
    assert manual.provenance == "manual"
    with pytest.raises(ValidationError):
        ObservationCrop(frame_id="f", sensor_box=[0, 0, 10, 10], provenance="neither")


def test_manual_mark_request_needs_only_class_frame_and_box():
    request = ManualMarkRequest(target_class="outlet", frame_id="f", sensor_box=[0, 0, 10, 10])
    assert request.node_id is None
    assert request.review_status == "candidate"


def test_unlocalized_observations_ride_the_graph_without_inventing_geometry():
    observation = UnlocalizedObservation(
        id="00000000-0000-0000-0000-000000000004",
        target_class="television",
        frame_id="f9",
        sensor_box=[1, 2, 3, 4],
    )
    graph = SceneGraph(
        scan_id="00000000-0000-0000-0000-000000000002",
        nodes=[],
        unlocalized_observations=[observation],
    )
    dumped = graph.model_dump(mode="json")
    assert dumped["unlocalized_observations"][0]["provenance"] == "manual"
    assert SceneGraph.model_validate(dumped).unlocalized_observations == [observation]
    empty = SceneGraph(scan_id="00000000-0000-0000-0000-000000000002", nodes=[])
    assert "unlocalized_observations" not in empty.model_dump(mode="json")


def test_scope_manifest_hash_does_not_change_rows_by_reference():
    m = manifest(required=["a"], rows=[row("site:a", "a")])
    original = m.model_dump(mode="json")
    m.rows[0].reason = "changed after construction"
    assert m.model_dump(mode="json") != original
