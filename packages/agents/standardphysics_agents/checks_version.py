"""A fingerprint of everything that decides what a shop's results say.

The rule pack carries a version a person writes by hand, and nobody bumps it
when a check, a verification or a sentence changes. This hashes the files that
decide a finding instead: the rule pack and its ledger, the precedent corpus,
the source of the checks, the findings and the copy, and the pipeline code a
re-check measures with. An assessment records the fingerprint it was made
under, so a deploy that changes any of them can check every shop again rather
than leave results the current code would not give.
"""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path

import standardphysics_pipeline

PACKAGE = Path(__file__).parent
DECIDING = ("rules", "precedents", "checks", "assess.py", "findings.py", "copy.py", "compliance.py")
"""What a result depends on, relative to this package."""
PIPELINE = Path(standardphysics_pipeline.__file__).parent
BUILDS_THE_GRAPH = frozenset({"discovery", "textures", "splats", "render_efficiency", "blender_scripts",
                              "astra.py", "astra_frames.py", "astra_patches.py", "astra_photo_evidence.py",
                              "astra_prompt.py", "astra_response.py", "astra_transport.py",
                              "blender.py", "check_blender.py", "ingest.py"})
"""Pipeline code that makes a scan's graph rather than measuring it. A re-check measures the stored graph again
with everything else in the pipeline: routes, footprints, what blocks the floor."""
SUFFIXES = frozenset({".py", ".json"})


def _deciding_files() -> list[Path]:
    found: list[Path] = []
    for name in DECIDING:
        path = PACKAGE / name
        found += sorted(p for p in path.rglob("*") if p.suffix in SUFFIXES) if path.is_dir() else [path]
    return found


def _measuring_files() -> list[Path]:
    return sorted(path for path in PIPELINE.rglob("*.py")
                  if "__pycache__" not in path.parts and path.relative_to(PIPELINE).parts[0] not in BUILDS_THE_GRAPH)


def _files() -> list[Path]:
    return _deciding_files() + _measuring_files()


def _name(path: Path) -> str:
    root = PACKAGE if path.is_relative_to(PACKAGE) else PIPELINE
    return f"{root.name}/{path.relative_to(root).as_posix()}"


@cache
def checks_version() -> str:
    digest = hashlib.sha256()
    for path in _files():
        digest.update(_name(path).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]
