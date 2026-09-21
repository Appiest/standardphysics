"""The rule pack, and the gate that keeps an agent out of it."""

from __future__ import annotations

import pytest
from standardphysics_agents import VerificationLedger, load_ledger
from standardphysics_agents.rules.pack import parse_pack

TIER_1_CHECKS = {
    "route_clear_width",
    "turn_clear_width",
    "passing_space",
    "turning_space",
    "door_clear_width",
    "service_counter_height",
    "service_counter_approach",
    "point_of_sale_height",
    "exit_path",
}

SCAN_CANNOT_SEE = {
    "entrance_threshold",
    "door_hardware",
    "door_opening_force",
    "door_maneuvering_clearance",
    "floor_surface",
    "protruding_objects",
    "reach_range",
    "dining_surface_height",
    "restroom_turning_space",
}


def test_every_rule_in_the_lane_document_is_in_the_pack(pack):
    ids = {rule.id for rule in pack.rules}
    assert TIER_1_CHECKS <= ids
    assert SCAN_CANNOT_SEE <= ids


def test_every_rule_carries_a_citation_and_the_sentence_behind_it(pack):
    for rule in pack.rules:
        assert rule.citation.section, rule.id
        assert rule.citation.edition, rule.id
        assert len(rule.source_text) > 40, rule.id


def test_thresholds_are_the_numbers_the_standard_states(pack):
    expected = {
        "route_clear_width": 36.0,
        "turn_clear_width": 48.0,
        "passing_space": 60.0,
        "turning_space": 60.0,
        "door_clear_width": 32.0,
        "service_counter_height": 36.0,
        "service_counter_approach": 48.0,
        "point_of_sale_height": 36.0,
        "door_opening_force": 5.0,
    }
    for rule_id, threshold in expected.items():
        assert pack.by_id(rule_id).threshold == threshold, rule_id


def test_units_are_the_units_the_standard_is_written_in(pack):
    assert pack.by_id("door_opening_force").unit == "lbf"
    assert pack.by_id("route_clear_width").unit == "in"


def test_a_height_is_a_maximum_and_a_width_is_a_minimum(pack):
    assert pack.by_id("service_counter_height").comparison == "at_most"
    assert pack.by_id("point_of_sale_height").comparison == "at_most"
    assert pack.by_id("route_clear_width").comparison == "at_least"


def test_a_measurement_on_the_threshold_passes(pack):
    rule = pack.by_id("route_clear_width")
    assert rule.satisfied_by(36.0)
    assert rule.satisfied_by(36.0000001)
    assert not rule.satisfied_by(35.9)


def test_a_maximum_is_compared_the_other_way(pack):
    rule = pack.by_id("service_counter_height")
    assert rule.satisfied_by(36.0)
    assert rule.satisfied_by(30.0)
    assert not rule.satisfied_by(43.3)


def test_a_missing_parameter_raises_rather_than_defaulting(pack):
    with pytest.raises(KeyError):
        pack.by_id("route_clear_width").parameter("not_a_real_number")


def test_duplicate_rule_ids_are_rejected(pack):
    payload = {
        "version": "test",
        "rules": [
            pack.by_id("route_clear_width").model_dump(),
            pack.by_id("route_clear_width").model_dump(),
        ],
    }
    with pytest.raises(ValueError, match="twice"):
        parse_pack(payload)


def test_an_unknown_field_in_the_pack_is_rejected(pack):
    payload = {"version": "test", "rules": [
        {**pack.by_id("door_clear_width").model_dump(), "threshold_inches": 32.0}
    ]}
    with pytest.raises(Exception):
        parse_pack(payload)


def test_no_check_is_enabled_until_a_person_verifies_it(pack):
    assert pack.enabled(VerificationLedger(), max_tier=1) == []


def test_the_shipped_ledger_enables_nothing_an_agent_wrote(pack):
    """Whatever is in the ledger on disk, every enabled rule has an entry."""
    shipped = load_ledger()
    for rule in pack.enabled(shipped, max_tier=3):
        assert shipped.entry_for(rule) is not None


def test_verifying_a_rule_enables_it(pack):
    rule = pack.by_id("door_clear_width")
    book = VerificationLedger().record(rule, verified_by="Dana")
    assert book.verifies(rule)
    assert rule in pack.enabled(book, max_tier=1)


def test_moving_a_threshold_invalidates_its_verification(pack):
    """The enforcement behind "no agent changes a threshold"."""
    rule = pack.by_id("route_clear_width")
    book = VerificationLedger().record(rule, verified_by="Dana")
    loosened = rule.model_copy(update={"threshold": 30.0})
    assert not book.verifies(loosened)


def test_changing_a_cited_section_invalidates_its_verification(pack):
    rule = pack.by_id("route_clear_width")
    book = VerificationLedger().record(rule, verified_by="Dana")
    moved = rule.model_copy(
        update={"citation": rule.citation.model_copy(update={"section": "403.5.9"})}
    )
    assert not book.verifies(moved)


def test_a_second_person_can_be_recorded(pack):
    rule = pack.by_id("door_clear_width")
    book = VerificationLedger().record(rule, verified_by="Dana")
    assert not book.double_checked(rule)
    book = book.second_check(rule, checked_by="Sam")
    assert book.double_checked(rule)
    assert book.reviewers() == ["Dana", "Sam"]


def test_a_second_check_needs_a_first_one(pack):
    with pytest.raises(KeyError):
        VerificationLedger().second_check(pack.by_id("door_clear_width"), "Sam")


def test_tier_2_and_3_rules_stay_out_of_a_tier_1_run(pack, ledger):
    enabled = {rule.id for rule in pack.enabled(ledger, max_tier=1)}
    assert "protruding_objects" not in enabled
    assert "reach_range" not in enabled
    assert "route_clear_width" in enabled


def test_the_contract_pack_carries_verification_across(pack, ledger):
    contract = pack.as_contract_pack(ledger)
    assert contract.version == pack.version
    assert all(check.verified_by_human for check in contract.checks)
    assert {c.id for c in contract.checks} >= TIER_1_CHECKS


def test_the_contract_pack_carries_each_rule_in_its_own_unit(pack, ledger):
    checks = {check.id: check for check in pack.as_contract_pack(ledger).checks}
    assert checks["door_opening_force"].unit == "lbf"
    assert checks["route_clear_width"].unit == "in"
