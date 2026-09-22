"""Regression and adversarial tests for the independent receipt verifier.

Mirrors the FIRST section of docs/deepseek-shop-pilot/05-adversarial-tests.txt:
the old G9 receipt shape must fail, a digest for a nonexistent file must fail,
a changed file with an old digest must fail, an empty graph claimed to contain
an outlet must fail, and a wrong-owner/wrong-revision artifact must fail.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

from scripts.shop_pilot.certificates import evaluate
from scripts.shop_pilot.evidence import sha256_bytes, sha256_file
from scripts.shop_pilot.receipt_verifier import verify_receipt

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
POLICY = REPO_ROOT / "docs" / "deepseek-shop-pilot" / "04-hard-gates.json"


def _artifact(path: pathlib.Path, relative: str) -> dict:
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "content_type": "application/json",
    }


def _head() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True)
    return result.stdout.strip()


def _base_receipt(tmp_path: pathlib.Path, gate_id: str = "G01", kind: str = "synthetic_component") -> dict:
    result = tmp_path / "run.json"
    result.write_text(json.dumps({"nodes": []}), encoding="utf-8")
    log = tmp_path / "run.log"
    log.write_text("1 passed\n", encoding="utf-8")
    output = _artifact(result, "run.json")
    return {
        "receipt_id": "R-001",
        "gate_id": gate_id,
        "run_id": "opencode-20260921-170615",
        "lane_id": "K",
        "evidence_kind": kind,
        "source_commit": _head(),
        "dirty_source_digest": sha256_bytes(b"clean"),
        "contract_hash": sha256_bytes(b"contract"),
        "policy_hash": sha256_file(POLICY),
        "input_artifacts": [],
        "output_artifacts": [output],
        "scan_id": None,
        "revision_id": None,
        "scenario_hash": None,
        "scope_manifest_hash": None,
        "evidence_manifest_hash": None,
        "command_or_recorded_ui_steps": "python -m pytest packages/agents/tests -q",
        "working_directory": str(tmp_path),
        "environment_versions": {"python": "3.11"},
        "started_at": "2026-09-21T17:00:00-07:00",
        "finished_at": "2026-09-21T17:00:05-07:00",
        "exit_code": 0,
        "assertions": [
            {
                "id": "hash",
                "measurement_method": "file_sha256",
                "expected": output["sha256"],
                "observed": output["sha256"],
                "evidence_paths": ["run.json"],
            },
            {
                "id": "command",
                "measurement_method": "command_matches",
                "expected": True,
                "observed": True,
                "params": {"required_substrings": ["pytest"]},
                "evidence_paths": [],
            },
        ],
        "raw_log_path": "run.log",
        "evaluator_identity": {"actor": "lane-Q", "attestation": "independent verification"},
    }


def test_valid_receipt_passes(tmp_path):
    receipt = _base_receipt(tmp_path)
    result = verify_receipt(receipt, artifacts_dir=tmp_path, policy_path=POLICY, git_worktree=REPO_ROOT)
    assert result["status"] == "valid", result


def test_old_g9_rebuilt_graph_shape_is_invalid(tmp_path):
    receipt = _base_receipt(tmp_path)
    receipt["output_artifacts"] = [{"path": "graph.json", "sha256": "100", "size_bytes": 1}]
    receipt["assertions"] = []
    result = verify_receipt(receipt, artifacts_dir=tmp_path)
    assert result["status"] == "invalid"
    assert any("sha256" in reason for reason in result["invalid_reasons"])


def test_old_g9_prose_artifact_map_is_invalid(tmp_path):
    receipt = _base_receipt(tmp_path)
    receipt["output_artifacts"] = {"rebuilt_graph": "100"}
    receipt["assertions"] = []
    result = verify_receipt(receipt, artifacts_dir=tmp_path)
    assert result["status"] == "invalid"


def test_digest_for_nonexistent_file_is_invalid(tmp_path):
    receipt = _base_receipt(tmp_path)
    receipt["output_artifacts"] = [{"path": "missing.json", "sha256": "a" * 64, "size_bytes": 10}]
    result = verify_receipt(receipt, artifacts_dir=tmp_path)
    assert result["status"] == "invalid"
    assert any("does not exist" in reason for reason in result["invalid_reasons"])


def test_changed_file_with_old_digest_is_invalid(tmp_path):
    receipt = _base_receipt(tmp_path)
    (tmp_path / "run.json").write_text(json.dumps({"nodes": [{"kind": "outlet"}]}), encoding="utf-8")
    result = verify_receipt(receipt, artifacts_dir=tmp_path)
    assert result["status"] == "invalid"
    assert any("sha256" in reason or "size" in reason for reason in result["invalid_reasons"])


def test_empty_graph_claimed_to_contain_outlet_is_invalid(tmp_path):
    receipt = _base_receipt(tmp_path)
    receipt["output_artifacts"] = [_artifact(tmp_path / "run.json", "run.json")]
    receipt["assertions"] = [
        {
            "id": "outlet-count",
            "measurement_method": "graph_kind_count",
            "expected": 1,
            "observed": 1,
            "params": {"kind": "outlet"},
            "evidence_paths": ["run.json"],
        }
    ]
    result = verify_receipt(receipt, artifacts_dir=tmp_path)
    assert result["status"] == "invalid"
    assert any("outlet-count" in reason for reason in result["invalid_reasons"])


def test_wrong_revision_artifact_is_invalid(tmp_path):
    graph = tmp_path / "run.json"
    graph.write_text(json.dumps({"scan_id": "s-1", "revision_id": 7, "nodes": []}), encoding="utf-8")
    receipt = _base_receipt(tmp_path, gate_id="G10", kind="saved_real_capture")
    receipt["scan_id"] = "s-1"
    receipt["revision_id"] = 4
    receipt["output_artifacts"] = [_artifact(graph, "run.json")]
    receipt["assertions"] = [
        {
            "id": "identity",
            "measurement_method": "identity_consistent",
            "expected": True,
            "observed": True,
            "params": {"fields": ["scan_id", "revision_id"]},
            "evidence_paths": ["run.json"],
        }
    ]
    result = verify_receipt(receipt, artifacts_dir=tmp_path)
    assert result["status"] == "invalid"
    assert any("identity" in reason for reason in result["invalid_reasons"])


def test_unknown_assertion_method_is_invalid(tmp_path):
    receipt = _base_receipt(tmp_path)
    receipt["assertions"] = [
        {"id": "trust-me", "measurement_method": "agent_says_so", "expected": True, "observed": True}
    ]
    result = verify_receipt(receipt, artifacts_dir=tmp_path)
    assert result["status"] == "invalid"
    assert any("no independent recompute handler" in reason for reason in result["invalid_reasons"])


def test_fabricated_policy_hash_is_invalid(tmp_path):
    receipt = _base_receipt(tmp_path)
    receipt["policy_hash"] = "b" * 64
    result = verify_receipt(receipt, artifacts_dir=tmp_path, policy_path=POLICY)
    assert result["status"] == "invalid"
    assert any("policy_hash" in reason for reason in result["invalid_reasons"])


def test_physical_evidence_without_identity_check_is_blocked(tmp_path):
    receipt = _base_receipt(tmp_path, gate_id="G10", kind="saved_real_capture")
    receipt["scan_id"] = "s-1"
    receipt["revision_id"] = 4
    result = verify_receipt(receipt, artifacts_dir=tmp_path)
    assert result["status"] == "externally_blocked"


def test_secret_printing_command_is_invalid(tmp_path):
    receipt = _base_receipt(tmp_path)
    receipt["command_or_recorded_ui_steps"] = "python3 -c 'import os; print(os.getenv(\"DISCOVERY_API_KEY\"))'"
    result = verify_receipt(receipt, artifacts_dir=tmp_path)
    assert result["status"] == "invalid"
    assert any("command" in reason for reason in result["invalid_reasons"])


def test_human_review_cannot_use_self_review_identity(tmp_path):
    receipt = _base_receipt(tmp_path, gate_id="G14", kind="human_rule_review")
    receipt["evaluator_identity"] = {"actor": "self_review", "attestation": "trust me"}
    result = verify_receipt(receipt, artifacts_dir=tmp_path)
    assert result["status"] == "invalid"


def test_synthetic_receipt_cannot_pass_physical_gate(tmp_path):
    receipt = _base_receipt(tmp_path, gate_id="G10", kind="synthetic_production_path")
    receipt["receipt_id"] = "R-synth"
    evaluation = evaluate([receipt], artifacts_dir=tmp_path)
    gate = next(g for g in evaluation["gates"] if g["id"] == "G10")
    assert gate["status"] != "passed"


def test_g9_certificate_blocked_by_invalid_receipt(tmp_path):
    receipt = _base_receipt(tmp_path, gate_id="G09", kind="synthetic_production_path")
    receipt["receipt_id"] = "R-g9"
    receipt["output_artifacts"] = [{"path": "graph.json", "sha256": "a" * 64, "size_bytes": 1}]
    receipt["assertions"] = []
    evaluation = evaluate([receipt], artifacts_dir=tmp_path)
    gate = next(g for g in evaluation["gates"] if g["id"] == "G09")
    assert gate["status"] == "failed"
