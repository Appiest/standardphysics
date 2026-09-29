"""No hand-written source file grows past MAX_LINES.

A file that long almost always holds more than one job, and a reviewer has to
read all of it to change any of it. Split it by responsibility instead of
raising the limit. Generated files are exempt because nobody edits them.
"""

from __future__ import annotations

import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAX_LINES = 800
SOURCE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".mjs", ".swift", ".sh"})
GENERATED = frozenset({"apps/web/src/types/contracts.ts"})


def _tracked_sources() -> list[str]:
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.split("\n")
    return [
        path
        for path in tracked
        if pathlib.PurePosixPath(path).suffix in SOURCE_SUFFIXES and path not in GENERATED
    ]


def _line_count(path: str) -> int:
    with (ROOT / path).open("rb") as source:
        return sum(1 for _ in source)


def test_no_source_file_is_longer_than_the_limit():
    too_long = {
        path: lines
        for path in _tracked_sources()
        if (ROOT / path).exists() and (lines := _line_count(path)) > MAX_LINES
    }
    assert not too_long, f"split these files below {MAX_LINES} lines: {too_long}"


def test_every_generated_exemption_still_exists():
    missing = sorted(path for path in GENERATED if not (ROOT / path).exists())
    assert not missing, f"drop these from GENERATED, they are gone: {missing}"
