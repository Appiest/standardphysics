"""The rules commands: read a threshold's section, then turn its check on."""

from __future__ import annotations

import math
import sys

from ..rules import RuleSpec, load_ledger, load_pack, save_ledger

READ_BACK_TOLERANCE = 1e-9


def _state(rule: RuleSpec, ledger) -> str:
    entry = ledger.entry_for(rule)
    if entry is None:
        return "waiting on a person"
    if entry.second_check_by:
        return f"verified by {entry.verified_by}, checked by {entry.second_check_by}"
    return f"verified by {entry.verified_by}, wants a second reader"


def list_rules(args) -> int:
    pack, ledger = load_pack(), load_ledger()
    for rule in pack.within_tier(args.tier):
        print(
            f"{rule.id:28} tier {rule.tier}  "
            f"{rule.threshold:>6g} {rule.unit:<4} {rule.comparison:<9} "
            f"{rule.citation.display():<48} {_state(rule, ledger)}"
        )
    return 0


def show_rule(args) -> int:
    _print_rule(load_pack().by_id(args.rule_id), load_ledger())
    return 0


def _print_rule(rule: RuleSpec, ledger) -> None:
    print(f"{rule.id}\n")
    print(f"  {rule.title}")
    print(f"  {rule.citation.authority} {rule.citation.display()}")
    print(f"  {rule.citation.url or ''}")
    print(f"  threshold: {rule.threshold:g} {rule.unit} ({rule.comparison})")
    for name, value in sorted(rule.parameters.items()):
        print(f"  {name}: {value:g}")
    print(f"  evidence: {rule.evidence}")
    print(f"  state: {_state(rule, ledger)}\n")
    print(f"  {rule.source_text}\n")
    if rule.review_note:
        print(f"  review note: {rule.review_note}\n")


def _read_back(rule: RuleSpec, given: float | None) -> bool:
    if given is not None:
        return math.isclose(given, rule.threshold, abs_tol=READ_BACK_TOLERANCE)
    typed = input(f"Type the number you read in {rule.citation.section}: ").strip()
    return _matches(typed, rule)


def verify_rule(args) -> int:
    pack, ledger = load_pack(), load_ledger()
    rule = pack.by_id(args.rule_id)
    _print_rule(rule, ledger)
    if not _read_back(rule, args.threshold):
        print(
            f"That is not the threshold in the pack. {rule.id} stays off.",
            file=sys.stderr,
        )
        return 1
    save_ledger(ledger.record(rule, verified_by=args.by, note=args.note))
    print(f"{rule.id} is on. {rule.threshold:g} {rule.unit}, read by {args.by}.")
    return 0


def review_rules(args) -> int:
    """Walk every rule nobody has read yet, one section at a time."""
    pack = load_pack()
    waiting = [r for r in pack.within_tier(args.tier) if not load_ledger().verifies(r)]
    if not waiting:
        print(f"Every tier {args.tier} rule has a reader.")
        return 0

    print(f"{len(waiting)} rules to read. Enter a blank line to stop.\n")
    for rule in waiting:
        if _review_one(rule, args.by) is False:
            break
    return 0


def _review_one(rule: RuleSpec, reviewer: str) -> bool:
    _print_rule(rule, load_ledger())
    typed = input(
        f"Number you read in {rule.citation.section} (blank to stop): "
    ).strip()
    if not typed:
        return False
    if not _matches(typed, rule):
        print(f"  That is not what the pack says. {rule.id} stays off.\n")
        return True
    save_ledger(load_ledger().record(rule, verified_by=reviewer))
    print(f"  {rule.id} is on.\n")
    return True


def _matches(typed: str, rule: RuleSpec) -> bool:
    try:
        return math.isclose(float(typed), rule.threshold, abs_tol=READ_BACK_TOLERANCE)
    except ValueError:
        return False


def second_check_rule(args) -> int:
    pack, ledger = load_pack(), load_ledger()
    rule = pack.by_id(args.rule_id)
    save_ledger(ledger.second_check(rule, checked_by=args.by))
    print(f"{rule.id} has two readers.")
    return 0
