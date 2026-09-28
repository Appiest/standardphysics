"""Corpus completeness and room evidence remain separate from legal sign-off."""

from __future__ import annotations

from datetime import UTC, datetime

import standardphysics_agents.compliance as compliance
from standardphysics_agents import VerificationLedger, evaluate_candidate_room
from standardphysics_agents.precedents import PrecedentLedger, PrecedentVerification, load_precedents
from standardphysics_contracts import Mat4
from standardphysics_contracts.precedents import SpaceTypology
from standardphysics_fixtures.shop import node_id

CORPUS = load_precedents(allow_unverified=True)
TYPOLOGY = SpaceTypology.QSR_BEVERAGE


def _directive_ledger() -> PrecedentLedger:
    return PrecedentLedger({directive.directive_id: PrecedentVerification(
        directive_id=directive.directive_id,
        verified_by="Human reviewer in test fixture",
        verified_at=datetime(2026, 1, 1, tzinfo=UTC),
    ) for directive in CORPUS})


def _reviewed_rules(pack) -> VerificationLedger:
    ledger = VerificationLedger()
    for rule in pack.rules:
        ledger = ledger.record(rule, "Human reviewer in test fixture")
    return ledger


def _evaluate(graph, scenario, pipeline, pack, *, base=None, reviewed=True):
    return evaluate_candidate_room(
        graph, scenario, pipeline, TYPOLOGY, base=base, rules=pack,
        rule_ledger=_reviewed_rules(pack) if reviewed else VerificationLedger(),
        directives=CORPUS, directive_ledger=_directive_ledger() if reviewed else PrecedentLedger({}),
    )


def _entries(result):
    return {entry.id: entry for entry in result.entries}


def test_every_corpus_requirement_has_a_stable_entry(graph, scenario, pipeline, pack):
    result = _evaluate(graph, scenario, pipeline, pack)
    expected = {f"rule:{rule.id}" for rule in pack.rules}
    for directive in CORPUS:
        expected.update(f"query:{directive.directive_id}:{query.query_id}" for query in directive.inspection_queries)
        expected.update(f"constraint:{directive.directive_id}:{name}" for name, _, _ in
                        compliance._constraint_specs(directive))
    assert set(_entries(result)) == expected
    assert all(entry.outcome in {"pass", "fail", "unknown", "unverified"}
               for entry in result.entries if entry.applicable)
    assert all(entry.reason for entry in result.entries if not entry.applicable)
    assert not result.accept_for_final_layout


def test_shared_rule_id_keeps_height_and_length_separate(graph, scenario, pipeline, pack):
    entries = _entries(_evaluate(graph, scenario, pipeline, pack))
    height = entries["query:service_counter:service_counter_height"]
    length = entries["query:service_counter:service_counter_clear_length"]
    assert height.outcome == "fail"
    assert length.outcome == "pass"
    assert height.evidence[0].measured_value != length.evidence[0].measured_value


def test_route_evidence_names_the_scenario_leg(graph, scenario, pipeline, pack):
    route = _entries(_evaluate(graph, scenario, pipeline, pack))["rule:route_clear_width"]
    assert any(scenario.stops[0].name in item.target for item in route.evidence)


def test_moving_a_fixed_counter_adds_a_constraint_failure(graph, scenario, pipeline, pack):
    counter = graph.by_id(node_id("counter"))
    at = counter.transform.position
    moved = counter.model_copy(update={"transform": Mat4.translation(at.x + 0.2, at.y, at.z)})
    candidate = graph.model_copy(update={"nodes": [moved if node.id == counter.id else node
                                                   for node in graph.nodes]})
    before = _entries(_evaluate(graph, scenario, pipeline, pack, base=graph))
    after = _entries(_evaluate(candidate, scenario, pipeline, pack, base=graph))
    key = "constraint:service_counter:fixed_role:service_counter"
    assert before[key].outcome == "pass"
    assert after[key].outcome == "fail"


def test_missing_measurement_and_lost_checker_coverage_are_unknown(graph, scenario, pipeline, pack, monkeypatch):
    class MissingCounterHeight(type(pipeline)):
        def counter_height(self, graph, counter_id):
            raise NotImplementedError("counter height unavailable")

    missing = _entries(_evaluate(graph, scenario, MissingCounterHeight(), pack))
    assert missing["rule:service_counter_height"].outcome == "unknown"
    assert missing["query:service_counter:service_counter_height"].outcome == "unknown"

    monkeypatch.setattr(compliance, "REGISTRY", tuple(
        item for item in compliance.REGISTRY if "route_clear_width" not in item[0]
    ))
    lost = _entries(_evaluate(graph, scenario, pipeline, pack))
    assert lost["rule:route_clear_width"].outcome == "unknown"
    assert lost["query:circulation_clear_width:route_clear_width"].outcome == "unknown"


def test_uncertain_scan_geometry_cannot_pass_a_query(graph, scenario, pipeline, pack):
    counter = graph.by_id(node_id("counter"))
    uncertain = counter.model_copy(update={"quality": "needs_another_look"})
    candidate = graph.model_copy(update={"nodes": [uncertain if node.id == counter.id else node
                                                   for node in graph.nodes]})
    entries = _entries(_evaluate(candidate, scenario, pipeline, pack))
    assert entries["rule:service_counter_height"].outcome == "unknown"
    assert entries["query:service_counter:service_counter_clear_length"].outcome == "unknown"


def test_unimplemented_directive_query_is_unknown(graph, scenario, pipeline, pack):
    result = _evaluate(graph, scenario, pipeline, pack)
    entry = _entries(result)["query:accessible_dining_surfaces:dining_knee_clearance_height"]
    assert entry.outcome == "unknown"
    assert entry.implementation is None
    assert entry.citation == "ADA_2010_306.3.1"
    assert entry.id in result.unknown


def test_terminal_alias_keeps_kiosk_queries_applicable(graph, scenario, pipeline, pack):
    node = graph.nodes[-1]
    terminal = node.model_copy(update={"label": "terminal", "raw_category": "terminal"})
    candidate = graph.model_copy(update={"nodes": [*graph.nodes[:-1], terminal]})
    entry = _entries(_evaluate(candidate, scenario, pipeline, pack))[
        "query:self_service_kiosk:kiosk_operable_part_height"
    ]
    assert entry.applicable
    assert entry.outcome == "unknown"


def test_preview_only_rules_and_unsigned_directives_remain_unverified(graph, scenario, pipeline, pack):
    result = _evaluate(graph, scenario, pipeline, pack, reviewed=False)
    assert _entries(result)["rule:route_clear_width"].outcome == "unverified"
    assert _entries(result)["query:service_counter:service_counter_height"].outcome == "unverified"
    assert result.unverified
    assert not result.accept_for_final_layout


def test_measurement_is_kept_apart_from_sign_off(graph, scenario, pipeline, pack):
    signed = _evaluate(graph, scenario, pipeline, pack)
    unsigned = _evaluate(graph, scenario, pipeline, pack, reviewed=False)
    for entry in (entry for entry in signed.entries if entry.applicable):
        assert entry.measured == entry.outcome
    assert [entry.measured for entry in unsigned.entries] == [entry.measured for entry in signed.entries]
    assert unsigned.measured_failed == signed.failed
    assert unsigned.measured_unknown == signed.unknown


def test_each_measured_target_keeps_its_own_result(graph, scenario, pipeline, pack):
    entries = _entries(_evaluate(graph, scenario, pipeline, pack, reviewed=False))
    route = entries["rule:route_clear_width"]
    assert route.evidence and all(item.measured in {"pass", "fail", "unknown"} for item in route.evidence)
    assert (route.measured == "fail") == any(item.measured == "fail" for item in route.evidence)


def test_inapplicable_directive_records_why(graph, scenario, pipeline, pack):
    entries = _entries(_evaluate(graph, scenario, pipeline, pack))
    door = entries["query:door_clearances:door_clear_width"]
    assert not door.applicable
    assert door.outcome is None
    assert "typology" in door.reason
    dispenser = entries["query:service_counter:self_service_dispenser_reach"]
    assert not dispenser.applicable
    assert "target role" in dispenser.reason


def test_incomplete_scan_evidence_never_claims_final_acceptance(graph, scenario, pipeline, pack):
    result = _evaluate(graph, scenario, pipeline, pack)
    assert result.unknown
    assert not result.all_verified_measurable_passed
    assert not result.accept_for_final_layout
    assert "query:accessible_dining_surfaces:dining_knee_clearance_height" in result.unknown
