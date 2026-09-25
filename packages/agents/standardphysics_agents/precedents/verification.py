"""Verification ledger and gate for ADA layout directives.

An agent may not enable a directive on its own. A person records that they read
the ADA sections a directive cites and confirmed its thresholds against them.
Case references carry their own sign-off inside the corpus.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from standardphysics_contracts.precedents import PrecedentDirective

DATA_DIR = Path(__file__).resolve().parent / "data"
PRECEDENTS_FILE = DATA_DIR / "precedents.v1.json"
LEDGER_FILE = DATA_DIR / "precedent_verification.json"
LEDGER_ENV = "STANDARDPHYSICS_PRECEDENT_LEDGER"

PREVIEW_REVIEWER = "unverified preview (development only)"


class PrecedentVerification(BaseModel):
    """One person confirming one directive against the sections it cites."""

    model_config = ConfigDict(extra="forbid")

    directive_id: str
    verified_by: str
    verified_at: datetime
    second_check_by: str | None = None

    @property
    def is_preview(self) -> bool:
        return self.verified_by.strip() == PREVIEW_REVIEWER


class PrecedentLedger:
    def __init__(self, verifications: dict[str, PrecedentVerification]):
        self._entries = dict(verifications)

    def is_verified(self, directive_id: str, allow_preview: bool = False) -> bool:
        entry = self._entries.get(directive_id)
        if entry is None:
            return False
        if entry.is_preview and not allow_preview:
            return False
        return True

    def get(self, directive_id: str) -> PrecedentVerification | None:
        return self._entries.get(directive_id)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries.values())


def load_precedent_ledger(path: Path | None = None) -> PrecedentLedger:
    target = path or (Path(os.environ[LEDGER_ENV]) if LEDGER_ENV in os.environ else LEDGER_FILE)
    if not target.exists():
        return PrecedentLedger({})
    raw = json.loads(target.read_text(encoding="utf-8"))
    entries = {}
    for item in raw:
        v = PrecedentVerification.model_validate(item)
        entries[v.directive_id] = v
    return PrecedentLedger(entries)


def load_precedents(
    path: Path | None = None,
    ledger: PrecedentLedger | None = None,
    allow_unverified: bool = False,
) -> list[PrecedentDirective]:
    """Load directives, dropping any no person has verified unless preview is enabled."""
    target = path or PRECEDENTS_FILE
    raw = json.loads(target.read_text(encoding="utf-8"))
    directives = [PrecedentDirective.model_validate(p) for p in raw.get("precedents", [])]

    if allow_unverified or os.environ.get("SP_PREVIEW_UNVERIFIED_PRECEDENTS") == "1":
        return directives

    active_ledger = ledger or load_precedent_ledger()
    return [d for d in directives if active_ledger.is_verified(d.directive_id)]
