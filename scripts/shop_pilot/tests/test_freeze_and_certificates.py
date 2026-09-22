"""Tests for the policy freeze and the gate/certificate evaluator."""

from __future__ import annotations

import json
import pathlib
import subprocess

from scripts.shop_pilot.certificates import evaluate
from scripts.shop_pilot.evidence import canonical_dirty_digest, sha256_bytes, sha256_file
from scripts.shop_pilot.freeze import build_freeze

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
POLICY = REPO_ROOT / "docs" / "deepseek-shop-pilot" / "04-hard-gates.json"


def test_freeze_records_policy_hash_and_holdout(tmp_path):
    freeze = build_freeze(REPO_ROOT, collect=False)
    assert freeze["policy_hash"] == sha256_file(POLICY)
    assert freeze["holdout_policy"]["frozen_before_any_tuning"] is True
    assert freeze["holdout_policy"]["g12_holdout"]["minimum_distinct_sites"] == 3
    assert "docs/deepseek-shop-pilot/01-coordinator.txt" in freeze["contract_hashes"]
    assert freeze["test_inventory"]


def _valid_receipt(tmp_path: pathlib.Path, gate: str, kind: str) -> dict:
    result = tmp_path / f"{gate}.json"
    result.write_text(json.dumps({"ok": True}), encoding="utf-8")
    log = tmp_path / f"{gate}.log"
    log.write_text("passed\n", encoding="utf-8")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()
    return {
        "receipt_id": f"R-{gate}", "gate_id": gate, "run_id": "run", "lane_id": "K",
        "evidence_kind": kind, "source_commit": head, "dirty_source_digest": canonical_dirty_digest({}),
        "dirty_source_files": {},
        "contract_hash": sha256_bytes(b"c"), "policy_hash": sha256_file(POLICY),
        "input_artifacts": [],
        "output_artifacts": [{
            "path": f"{gate}.json", "sha256": sha256_file(result),
            "size_bytes": result.stat().st_size, "content_type": "application/json",
        }],
        "scan_id": None, "revision_id": None, "scenario_hash": None,
        "scope_manifest_hash": None, "evidence_manifest_hash": None,
        "command_or_recorded_ui_steps": "python -m pytest services/api/tests -q",
        "working_directory": str(tmp_path), "environment_versions": {"python": "3.11"},
        "started_at": "2026-09-21T17:00:00-07:00", "finished_at": "2026-09-21T17:00:01-07:00",
        "exit_code": 0,
        "assertions": [{
            "id": "hash", "measurement_method": "file_sha256",
            "expected": sha256_file(result), "observed": sha256_file(result),
            "evidence_paths": [f"{gate}.json"],
        }],
        "raw_log_path": f"{gate}.log",
        "evaluator_identity": {"actor": "lane-Q", "attestation": "independent"},
    }


def test_gate_passes_with_valid_synthetic_evidence(tmp_path):
    receipt = _valid_receipt(tmp_path, "G01", "synthetic_component")
    evaluation = evaluate([receipt], artifacts_dir=tmp_path)
    gate = next(g for g in evaluation["gates"] if g["id"] == "G01")
    assert gate["status"] == "passed"


def test_software_certificate_requires_all_member_gates(tmp_path):
    receipt = _valid_receipt(tmp_path, "G01", "synthetic_component")
    evaluation = evaluate([receipt], artifacts_dir=tmp_path)
    assert evaluation["certificates"]["software_verified"]["status"] == "pending"


def test_g00_fails_without_required_mutation_kill_count(tmp_path):
    receipt = _valid_receipt(tmp_path, "G00", "synthetic_component")
    receipt["mutation_id"] = "M01"
    evaluation = evaluate([receipt], artifacts_dir=tmp_path)
    gate = next(g for g in evaluation["gates"] if g["id"] == "G00")
    assert gate["status"] == "failed"
    assert "18" in gate["reason"]
