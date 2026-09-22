"""Freeze the evaluation policy, test inventory and holdout selection.

The point is to lock what will be measured before any tuning happens. The
result records the exact hashes of the policy/contract documents, every test
file in the repository with its hash, and the declared holdout rule for the
G12 semantic benchmark. A later receipt whose ``policy_hash`` or
``contract_hash`` differs from this freeze is rejected by the verifier.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
from typing import Any

from .evidence import sha256_file

POLICY_DOC = "docs/deepseek-shop-pilot/04-hard-gates.json"
CONTRACT_DOCS = (
    "docs/deepseek-shop-pilot/01-coordinator.txt",
    "docs/deepseek-shop-pilot/02-contracts-and-ownership.txt",
    "docs/deepseek-shop-pilot/05-adversarial-tests.txt",
    "docs/deepseek-shop-pilot/06-phone-and-field-handoff.txt",
)
TEST_ROOTS = ("tests", "services/api/tests", "packages/agents/tests", "packages/pipeline/tests", "scripts/tests")


def _iter_test_files(root: pathlib.Path) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for relative in TEST_ROOTS:
        base = root / relative
        if base.is_dir():
            found.extend(sorted(base.rglob("test_*.py")))
    return sorted(set(found))


def test_inventory(root: pathlib.Path) -> dict[str, str]:
    return {str(path.relative_to(root)): sha256_file(path) for path in _iter_test_files(root)}


def collect_test_count(root: pathlib.Path) -> int | None:
    """Best-effort collected test count; None if collection is unavailable."""
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "--collect-only", "-q"],
            cwd=root, capture_output=True, text=True, timeout=300,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"(\d+) tests? collected", result.stdout)
    return int(match.group(1)) if match else None


def build_freeze(root: pathlib.Path, *, collect: bool = False) -> dict[str, Any]:
    policy = root / POLICY_DOC
    if not policy.is_file():
        raise FileNotFoundError(f"policy document not found: {policy}")
    return {
        "schema_version": 1,
        "kind": "shop_pilot_evaluation_freeze_not_results",
        "policy_doc": POLICY_DOC,
        "policy_hash": sha256_file(policy),
        "contract_hashes": {
            doc: sha256_file(root / doc) for doc in CONTRACT_DOCS if (root / doc).is_file()
        },
        "test_inventory": test_inventory(root),
        "collected_test_count": collect_test_count(root) if collect else None,
        "holdout_policy": {
            "tuning_sources": ["preserved Moffett captures"],
            "g12_holdout": {
                "minimum_distinct_sites": 3,
                "must_exclude_sites_used_for_tuning": True,
                "same_object_in_many_frames_counts_once": True,
                "default_2d_match_iou_min": 0.5,
                "one_to_one_matching_required": True,
                "misses_false_positives_abstentions_reported": True,
            },
            "frozen_before_any_tuning": True,
        },
    }
