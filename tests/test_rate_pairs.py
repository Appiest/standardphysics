"""The parts of scripts/finetune/rate_pairs.py that don't need a running server:
the left/right-to-a/b translation, the fixed-seed side assignment, and the
Q-agreement calculation `--score` reports."""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.finetune import rate_pairs  # noqa: E402


def test_picked_side_translates_left_and_right_by_which_was_shown_there():
    assert rate_pairs._picked_side("left", left_was="a") == "a"
    assert rate_pairs._picked_side("right", left_was="a") == "b"
    assert rate_pairs._picked_side("left", left_was="b") == "b"
    assert rate_pairs._picked_side("right", left_was="b") == "a"


def test_picked_side_keeps_a_tie_a_tie_regardless_of_side():
    assert rate_pairs._picked_side("tie", left_was="a") == "tie"
    assert rate_pairs._picked_side("tie", left_was="b") == "tie"


def test_left_is_a_is_deterministic_across_calls():
    for pair_id in ("rating-00", "rating-01", "rating-17", "rating-29"):
        first = rate_pairs.left_is_a(pair_id)
        second = rate_pairs.left_is_a(pair_id)
        assert first == second


def test_left_is_a_is_not_the_same_side_for_every_pair():
    assignments = {rate_pairs.left_is_a(f"rating-{i:02d}") for i in range(30)}
    assert assignments == {True, False}


def _pair(picked: str, a_q: float, b_q: float) -> dict:
    return {"picked": picked, "_pair": {"a": {"q": {"q": a_q}}, "b": {"q": {"q": b_q}}}}


def test_agreement_counts_a_match_when_the_pick_has_the_higher_q():
    rows = [_pair("a", 0.9, 0.3), _pair("b", 0.2, 0.8)]
    matches, total = rate_pairs._agreement(rows, "q")
    assert (matches, total) == (2, 2)


def test_agreement_counts_a_miss_when_the_pick_has_the_lower_q():
    rows = [_pair("a", 0.2, 0.9)]
    matches, total = rate_pairs._agreement(rows, "q")
    assert (matches, total) == (0, 1)


def test_agreement_skips_ties_in_the_human_pick():
    rows = [_pair("tie", 0.9, 0.3), _pair("a", 0.9, 0.3)]
    matches, total = rate_pairs._agreement(rows, "q")
    assert (matches, total) == (1, 1)


def test_agreement_skips_pairs_where_the_term_itself_is_tied():
    rows = [_pair("a", 0.5, 0.5), _pair("a", 0.9, 0.3)]
    matches, total = rate_pairs._agreement(rows, "q")
    assert (matches, total) == (1, 1)
