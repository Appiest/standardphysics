"""The shipped ledger is a preview, and nothing may call it a review.

A preview ledger enables the calculations so the product has something to
show before a person has read the sections. These tests pin the honesty
boundary: the preview marker never counts as a reviewer, a real reviewer's
name does, and the contract pack that the UI and the report consume says so
either way.
"""

from __future__ import annotations

import pytest
from standardphysics_agents import (
    VerificationLedger,
    load_ledger,
    load_pack,
)
from standardphysics_agents.rules.verification import PREVIEW_REVIEWER


def _book(pack):
    book = VerificationLedger()
    for rule in pack.rules:
        book = book.record(rule, verified_by="Taylor, who read it")
    return book


@pytest.fixture(scope="module")
def pack():
    return load_pack()


@pytest.fixture(scope="module")
def shipped():
    return load_ledger()


class TestShippedLedgerIsPreviewOnly:
    def test_it_never_claims_a_human_read_anything(self, pack, shipped):
        human = [e for e in shipped.entries if not e.is_preview]
        assert human == []

    def test_no_entry_names_a_second_checker(self, shipped):
        assert all(e.second_check_by is None for e in shipped.entries)

    def test_preview_entries_keep_the_checks_running(self, pack, shipped):
        """The preview enables calculations; it just never counts as review."""
        enabled = {r.id for r in pack.rules if shipped.verifies(r)}
        assert len(enabled) >= 10
        assert all(
            not shipped.personally_verified(r) for r in pack.rules if shipped.verifies(r)
        )

    def test_the_contract_pack_carries_no_human_claim(self, pack, shipped):
        contract = pack.as_contract_pack(shipped)
        assert not any(check.verified_by_human for check in contract.checks)


class TestRealReviewers:
    def test_a_named_person_is_not_preview(self, pack):
        rule = pack.by_id("route_clear_width")
        book = VerificationLedger().record(rule, verified_by="Ada H. Reviewer")
        assert book.personally_verified(rule)

    def test_a_person_review_reaches_the_contract_pack(self, pack):
        rule = pack.by_id("route_clear_width")
        book = VerificationLedger().record(rule, verified_by="Ada H. Reviewer")
        contract = pack.as_contract_pack(book)
        checked = next(c for c in contract.checks if c.id == rule.id)
        assert checked.verified_by_human
        unchecked = next(c for c in contract.checks if c.id != rule.id)
        assert not unchecked.verified_by_human

    def test_double_check_requires_a_person_and_a_second(self, pack):
        rule = pack.by_id("route_clear_width")
        book = VerificationLedger().record(rule, verified_by="Ada H. Reviewer")
        assert not book.double_checked(rule)
        book = book.second_check(rule, "B. Audit")
        assert book.double_checked(rule)

    def test_a_second_check_cannot_promote_a_preview_entry(self, pack, shipped):
        rule = pack.by_id("route_clear_width")
        book = shipped.second_check(rule, "B. Audit")
        assert not book.double_checked(rule)
        assert not book.personally_verified(rule)

    def test_a_preview_marker_is_never_a_reviewer_name(self):
        entry = VerificationLedger().record(
            load_pack().by_id("route_clear_width"), verified_by=PREVIEW_REVIEWER
        )
        assert entry.entries[0].is_preview


class TestNoInventedTelevisionRule:
    """A television's presence sets no universal mounting-height requirement.

    The pack ships no check about TVs, and no one may add one by accident:
    finding a screen in the room is not a legal condition about how high it
    hangs.
    """

    def test_no_rule_mentions_a_television(self, pack):
        for rule in pack.rules:
            text = " ".join([rule.id, rule.title, rule.source_text]).casefold()
            assert "television" not in text
            assert "tv " not in text
