"""ScopeRow semantics behind G06: no absent class disappears, no unknown passes.

These are producer-level tests of scope_manifest.build_scope_manifest against
the shared fixture, distinct from K's frozen API-level matrix tests: they pin
the item linkage, the coverage rows, the needs_verification mapping for
unevaluated rules, and the separation between calculation outcome and legal
review status.
"""

from __future__ import annotations

import pytest
from standardphysics_api.scope_manifest import (
    PILOT_TARGET_CLASSES,
    build_scope_manifest,
)
from standardphysics_agents import VerificationLedger, assess, load_ledger, load_pack
from standardphysics_agents.rules.verification import PREVIEW_REVIEWER
from standardphysics_fixtures import build_graph, build_scenario
from standardphysics_fixtures.shop import node_id
from standardphysics_pipeline import PipelineMeasurements

ALLOWED = {"satisfied", "violation", "needs_verification", "not_applicable", "unobserved"}


@pytest.fixture(scope="module")
def pack():
    return load_pack()


@pytest.fixture(scope="module")
def manifest(pack):
    graph = build_graph()
    scenario = build_scenario()
    book = load_ledger()  # the shipped preview ledger: 16 entries, 2 rules wait
    result = assess(
        graph,
        scenario,
        PipelineMeasurements(),
        rules=pack,
        ledger=book,
        max_tier=3,
    )
    checks = pack.enabled(book, max_tier=1)
    waiting = {gap.rule_id: gap.waiting_on for gap in result.unevaluated}
    return build_scope_manifest(graph, scenario, result.assessment, checks, waiting)


def test_every_requested_requirement_has_a_visible_row(manifest):
    present = {row.requirement_id for row in manifest.rows if row.requested}
    assert set(manifest.requested_requirements) <= present


def test_every_requested_class_has_a_coverage_row(manifest):
    for name in PILOT_TARGET_CLASSES:
        rows = [r for r in manifest.rows if r.item.item_slug == f"class:{name}"]
        assert len(rows) == 1, name
        assert rows[0].requested


def test_a_class_the_fixture_has_is_observed_coverage(manifest):
    row = next(r for r in manifest.rows if r.item.item_slug == "class:service_counter")
    assert row.item.observed
    assert row.outcome == "satisfied"
    assert row.item.source == "measured"


def test_absent_classes_stay_unobserved_not_absent(manifest):
    for class_name in ("outlet", "television", "restroom_entrance"):
        row = next(
            r for r in manifest.rows if r.item.item_slug == f"class:{class_name}"
        )
        assert row.outcome == "unobserved"
        assert "absence is not established" in row.reason


def test_all_outcomes_are_allowed_and_legal_status_is_separate(manifest):
    for row in manifest.rows:
        assert row.outcome in ALLOWED
        if row.outcome != "needs_verification":
            assert row.legal_review_status == "unreviewed_preview"


def test_no_row_becomes_not_applicable_on_its_own(manifest):
    for row in manifest.rows:
        if row.applicability == "not_applicable":
            raise AssertionError(row)
    assert True


def test_measured_findings_link_their_item(manifest):
    counter_id = node_id("counter")
    rows = [
        row
        for row in manifest.rows
        if row.requirement_id == "service_counter_height"
        and row.item.item_slug == f"service_counter_height:{counter_id}"
    ]
    assert rows, "the counter-height row must name the counter node it measured"
    assert rows[0].item.item_id == counter_id
    assert rows[0].item.item_kind == "object"
    assert rows[0].measurement is not None
    assert rows[0].measurement["bounds"] == "unknown"


def test_checks_with_no_measurement_stay_unobserved_with_next_action(manifest):
    unobserved = [row for row in manifest.rows if row.outcome == "unobserved"]
    assert any(row.item.observed is False for row in unobserved)
    assert all(row.item.source == "requested_not_observed" for row in unobserved
               if row.item.item_kind == "site")


def test_waiting_rules_are_needs_verification_not_gone(manifest, pack):
    missing = pack.by_id("turn_clear_width")
    rows = [row for row in manifest.rows if row.requirement_id == missing.id]
    # The shipped preview ledger does not include turn_clear_width, so it waits
    # on a reader and must appear as needs_verification.
    assert rows
    assert all(row.outcome == "needs_verification" for row in rows)
    assert all(row.legal_review_status == "needs_review" for row in rows)


def test_review_status_can_only_be_supplied_explicitly(pack):
    graph = build_graph()
    scenario = build_scenario()
    book = VerificationLedger()
    for rule in pack.rules:
        book = book.record(rule, verified_by="Early Bird Reviewer")
    result = assess(graph, scenario, PipelineMeasurements(),
                    rules=pack, ledger=book, max_tier=3)
    waiting = {}
    preset = build_scope_manifest(
        graph, scenario, result.assessment, pack.enabled(book, max_tier=1), waiting,
    )
    assert all(
        row.legal_review_status != "reviewer_supplied" for row in preset.rows
    ), "nothing may self-assign a reviewer"
    reviewed = build_scope_manifest(
        graph, scenario, result.assessment, pack.enabled(book, max_tier=1), waiting,
        reviews={"service_counter_height": "reviewer_supplied"},
    )
    supplied = [
        row
        for row in reviewed.rows
        if row.requirement_id == "service_counter_height"
    ]
    assert supplied
    assert all(row.legal_review_status == "reviewer_supplied" for row in supplied)


def test_the_manifest_hash_covers_rows(pack):
    graph = build_graph()
    scenario = build_scenario()
    book = VerificationLedger()
    for rule in pack.rules:
        book = book.record(rule, verified_by=PREVIEW_REVIEWER)
    result = assess(graph, scenario, PipelineMeasurements(),
                    rules=pack, ledger=book, max_tier=3)
    waiting = {}
    first = build_scope_manifest(
        graph, scenario, result.assessment, pack.enabled(book, max_tier=1), waiting,
    )
    second = build_scope_manifest(
        graph, scenario, result.assessment, pack.enabled(book, max_tier=1), waiting,
        reviews={"route_clear_width": "needs_review"},
    )
    assert first.manifest_hash != second.manifest_hash
