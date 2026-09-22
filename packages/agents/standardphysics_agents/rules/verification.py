"""A check is enabled by a person, not by an agent.

An entry here records that someone read the section and confirmed the number.
It binds the rule id, the section, the threshold and the unit together, so
editing a threshold in the rule pack invalidates its verification and the check
switches itself off. That is the enforcement behind "no agent changes a
threshold": there is no edit an agent can make that both moves a number and
keeps the check running.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from .pack import DATA_DIR, RuleSpec

LEDGER_FILE = DATA_DIR / "verification.json"

LEDGER_PATH_ENV = "STANDARDPHYSICS_VERIFICATION_LEDGER"
"""Where the ledger lives, when it lives outside the package.

A deployment keeps who-read-what with its own records rather than inside an
installed wheel, and the tests keep theirs in a temporary directory so nothing
they do can switch a check on for everybody else.
"""

THRESHOLD_TOLERANCE = 1e-9

PREVIEW_REVIEWER = "unverified preview (development only)"
"""The one identity a preview runs under.

A preview ledger enables the calculations so a shop owner can see what a
checked run would look like, but an entry under this name is not a person and
never counts as one: `is_preview` filters it out of every human-review claim.
Real reviewers record their own names, and a real conclusion lists them.
"""

PREVIEW_NAMES = frozenset(
    {
        PREVIEW_REVIEWER,
        "Automated threshold check (needs a person's second check)",
    }
)
"""Every marker string a preview run has ever shipped under.

The current marker is the first one. The second was the pilot's early
automated-alias preview identity, whose entries are the same thing under an
older name; `is_preview` reads both so no ledger that was written before the
rename can pass an automated alias off as a reviewer.
"""


class Verification(BaseModel):
    """One person, one section, one number."""

    model_config = {"extra": "forbid"}

    rule_id: str
    section: str
    threshold: float
    unit: str
    verified_by: str
    verified_at: datetime
    second_check_by: str | None = None
    second_check_at: datetime | None = None
    note: str | None = None

    @property
    def is_preview(self) -> bool:
        """Whether this entry was made by a preview marker, not a person."""
        return self.verified_by in PREVIEW_NAMES

    def matches(self, rule: RuleSpec) -> bool:
        return (
            self.rule_id == rule.id
            and self.section == rule.citation.section
            and self.unit == rule.unit
            and math.isclose(
                self.threshold, rule.threshold, rel_tol=0.0, abs_tol=THRESHOLD_TOLERANCE
            )
        )


class VerificationLedger(BaseModel):
    model_config = {"extra": "forbid"}

    entries: list[Verification] = []

    def entry_for(self, rule: RuleSpec) -> Verification | None:
        for entry in self.entries:
            if entry.matches(rule):
                return entry
        return None

    def verifies(self, rule: RuleSpec) -> bool:
        return self.entry_for(rule) is not None

    def personally_verified(self, rule: RuleSpec) -> bool:
        """Whether a person, not the preview marker, verified this rule."""
        entry = self.entry_for(rule)
        return entry is not None and not entry.is_preview

    def double_checked(self, rule: RuleSpec) -> bool:
        entry = self.entry_for(rule)
        return (
            entry is not None
            and not entry.is_preview
            and entry.second_check_by is not None
        )

    def record(self, rule: RuleSpec, verified_by: str, note: str | None = None):
        """A new ledger with this rule verified. The old one is left alone."""
        entry = Verification(
            rule_id=rule.id,
            section=rule.citation.section,
            threshold=rule.threshold,
            unit=rule.unit,
            verified_by=verified_by,
            verified_at=datetime.now(timezone.utc),
            note=note,
        )
        kept = [e for e in self.entries if e.rule_id != rule.id]
        return VerificationLedger(entries=[*kept, entry])

    def second_check(self, rule: RuleSpec, checked_by: str):
        entry = self.entry_for(rule)
        if entry is None:
            raise KeyError(f"{rule.id} has no first verification to check")
        updated = entry.model_copy(
            update={
                "second_check_by": checked_by,
                "second_check_at": datetime.now(timezone.utc),
            }
        )
        kept = [e for e in self.entries if e.rule_id != rule.id]
        return VerificationLedger(entries=[*kept, updated])

    def reviewers(self) -> list[str]:
        names: list[str] = []
        for entry in self.entries:
            for name in (entry.verified_by, entry.second_check_by):
                if name and name not in names:
                    names.append(name)
        return names


def ledger_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    configured = os.environ.get(LEDGER_PATH_ENV)
    return Path(configured) if configured else LEDGER_FILE


def load_ledger(path: Path | None = None) -> VerificationLedger:
    target = ledger_path(path)
    if not target.exists():
        return VerificationLedger()
    return VerificationLedger.model_validate_json(target.read_text(encoding="utf-8"))


def save_ledger(ledger: VerificationLedger, path: Path | None = None) -> None:
    target = ledger_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(json.loads(ledger.model_dump_json()), indent=2) + "\n",
        encoding="utf-8",
    )
