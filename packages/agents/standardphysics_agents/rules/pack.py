"""Rule packs hold thresholds in their original legal units.

Each rule carries the section it came from and the sentence a person has to
read to confirm it. Nothing here converts a number. Conversion happens once, at
the display boundary, so a rounded label never changes an acceptance threshold.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from standardphysics_contracts import Check, Citation, RulePack
from standardphysics_contracts.rules import Tier

Comparison = Literal["at_least", "at_most"]

ResolvedBy = Literal["moving_things", "changing_a_fixture"]
"""What it would take to clear a finding against this rule.

A route is too narrow because of where things are standing, and moving them
fixes it. A counter is too high because of what it is, and no rearrangement
changes that. The router needs to know which, or it points the fix agent at a
table and asks it to make the table shorter.
"""

Evidence = Literal["measured", "photo", "owner_report", "document"]
"""measured: geometry answers it.
photo: a person has to send a picture of something LiDAR cannot see.
owner_report: a person has to try it and tell us.
document: a plan or permit review.
"""

DATA_DIR = Path(__file__).parent / "data"
PACK_FILE = DATA_DIR / "rulepack.v1.json"

COMPARISON_EPSILON = 1e-6
"""Float hygiene, in inches. A measurement that lands on the threshold passes.

A millionth of an inch is far below what any of this measures, so this changes
no outcome a person could observe. It exists because an exact 36.0 from the
pipeline must not fail a `>= 36.0` test on a rounding artefact.
"""


class RuleSpec(BaseModel):
    """One threshold, in the units the standard is written in."""

    model_config = {"extra": "forbid"}

    id: str
    title: str
    """Plain language, for the owner. "The path is too narrow"."""

    citation: Citation
    tier: Tier = 1
    threshold: float
    unit: str = "in"
    comparison: Comparison = "at_least"
    parameters: dict[str, float] = {}
    """Every other number the section states, named as the section names it."""

    applies_to: list[str] = []
    evidence: Evidence = "measured"
    resolved_by: ResolvedBy = "moving_things"
    source_text: str
    """The sentence a person reads to confirm the threshold."""

    review_note: str | None = None

    @property
    def measurable(self) -> bool:
        return self.evidence == "measured"

    @property
    def rearrangeable(self) -> bool:
        """Whether moving furniture could clear a finding against this rule."""
        return self.resolved_by == "moving_things"

    def satisfied_by(self, measured: float) -> bool:
        if self.comparison == "at_most":
            return measured <= self.threshold + COMPARISON_EPSILON
        return measured >= self.threshold - COMPARISON_EPSILON

    def parameter(self, name: str) -> float:
        """Raises when a section's number is missing, rather than defaulting.

        A check that silently substitutes zero for a legal number is worse than
        a check that fails to load.
        """
        if name not in self.parameters:
            raise KeyError(f"{self.id} has no parameter {name!r}")
        return self.parameters[name]

    def as_check(self) -> Check:
        """The contract shape Lane D reads."""
        return Check(
            id=self.id,
            title=self.title,
            citation=self.citation,
            tier=self.tier,
            threshold=self.threshold,
            unit=self.unit,
            applies_to=self.applies_to,
            verified_by_human=False,
        )


class AgentRulePack(BaseModel):
    model_config = {"extra": "forbid"}

    version: str
    rules: list[RuleSpec]

    def by_id(self, rule_id: str) -> RuleSpec:
        for rule in self.rules:
            if rule.id == rule_id:
                return rule
        raise KeyError(rule_id)

    def within_tier(self, max_tier: Tier = 1) -> list[RuleSpec]:
        return [rule for rule in self.rules if rule.tier <= max_tier]

    def enabled(self, ledger, max_tier: Tier = 1) -> list[RuleSpec]:
        """Only rules a person has verified. See rules/verification.py."""
        return [r for r in self.within_tier(max_tier) if ledger.verifies(r)]

    def as_contract_pack(self, ledger) -> RulePack:
        """Hand Lane D every rule, in its own unit, with verification carried over."""
        checks = []
        for rule in self.rules:
            check = rule.as_check()
            checks.append(
                check.model_copy(
                    update={"verified_by_human": ledger.personally_verified(rule)}
                )
            )
        return RulePack(version=self.version, checks=checks)


def parse_pack(payload: dict) -> AgentRulePack:
    pack = AgentRulePack.model_validate(payload)
    _reject_duplicate_ids(pack)
    return pack


def _reject_duplicate_ids(pack: AgentRulePack) -> None:
    seen: set[str] = set()
    for rule in pack.rules:
        if rule.id in seen:
            raise ValueError(f"rule pack {pack.version} defines {rule.id} twice")
        seen.add(rule.id)


@lru_cache(maxsize=None)
def load_pack(path: Path | None = None) -> AgentRulePack:
    return parse_pack(json.loads((path or PACK_FILE).read_text(encoding="utf-8")))
