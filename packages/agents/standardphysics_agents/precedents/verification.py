"""Verification ledger and gate for ADA layout directives.

An agent may not enable a directive on its own. A person records that they read
the ADA sections a directive cites and confirmed its thresholds against them.
Case references carry their own sign-off inside the corpus.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
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


def _ledger_path(path: Path | None) -> Path:
    return path or (Path(os.environ[LEDGER_ENV]) if LEDGER_ENV in os.environ else LEDGER_FILE)


def _reviewer(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or cleaned == PREVIEW_REVIEWER:
        raise ValueError("a sign-off needs the name of the person who read the source")
    return cleaned


def load_precedent_ledger(path: Path | None = None) -> PrecedentLedger:
    target = _ledger_path(path)
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

    active_ledger = ledger if ledger is not None else load_precedent_ledger()
    return [d for d in directives if active_ledger.is_verified(d.directive_id)]


def record_directive_review(directive_id: str, verified_by: str, path: Path | None = None) -> PrecedentVerification:
    """Enable a directive: a person read the ADA sections it cites and confirmed its thresholds."""
    target = _ledger_path(path)
    entries = {entry.directive_id: entry for entry in load_precedent_ledger(target)}
    entry = PrecedentVerification(
        directive_id=directive_id, verified_by=_reviewer(verified_by), verified_at=datetime.now(UTC)
    )
    entries[directive_id] = entry
    target.write_text(
        json.dumps([json.loads(item.model_dump_json()) for item in entries.values()], indent=2) + "\n",
        encoding="utf-8",
    )
    return entry


def sign_case_reference(
    directive_id: str, citation: str, verified_by: str, path: Path | None = None
) -> None:
    """Mark one case reference as read: a person checked the citation and holding against the opinion."""
    target = path or PRECEDENTS_FILE
    raw = json.loads(target.read_text(encoding="utf-8"))
    directive = next((d for d in raw["precedents"] if d["directive_id"] == directive_id), None)
    if directive is None:
        raise KeyError(f"no directive {directive_id}")
    case = next((c for c in directive["case_references"] if c["citation"] == citation), None)
    if case is None:
        raise KeyError(f"{directive_id} has no case cited as {citation}")
    case["verified_by"] = _reviewer(verified_by)
    case["verified_at"] = datetime.now(UTC).isoformat()
    target.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
