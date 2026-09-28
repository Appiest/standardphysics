"""A fingerprint of everything that decides what a shop's results say.

The rule pack carries a version a person writes by hand, and nobody bumps it
when a check, a verification or a sentence changes. This hashes the files that
decide a finding instead: the rule pack and its ledger, the precedent corpus,
and the source of the checks, the findings and the copy. An assessment records
the fingerprint it was made under, so a deploy that changes any of them can
check every shop again rather than leave results the current code would not
give.
"""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path

PACKAGE = Path(__file__).parent
DECIDING = ("rules", "precedents", "checks", "assess.py", "findings.py", "copy.py", "compliance.py")
"""What a result depends on, relative to this package."""
SUFFIXES = frozenset({".py", ".json"})


def _files() -> list[Path]:
    found: list[Path] = []
    for name in DECIDING:
        path = PACKAGE / name
        found += sorted(p for p in path.rglob("*") if p.suffix in SUFFIXES) if path.is_dir() else [path]
    return found


@cache
def checks_version() -> str:
    digest = hashlib.sha256()
    for path in _files():
        digest.update(path.relative_to(PACKAGE).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]
