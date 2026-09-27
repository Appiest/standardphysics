"""Tests for the outlet repair acceptance verifier (VERIFY-01, VERIFY-02, VERIFY-03)."""

from __future__ import annotations

import json
import pathlib

import pytest

from scripts.verify_outlet_repair_acceptance import verify_acceptance


@pytest.fixture
def mock_repo(tmp_path: pathlib.Path):
    # Minimal contract
    contract = {
        "required_gate_ids": ["G0", "G1", "G8", "G9", "G10"],
        "required_receipt_fields": [
            "test_id", "gate_id", "evidence_kind", "source_hash",
            "input_hashes", "command_or_ui_action", "started_at",
            "finished_at", "exit_status_or_assertion_status",
            "expected_behavior", "actual_behavior",
            "artifact_paths_and_hashes", "review_identity"
        ],
        "cases": [
            {"id": "STATUS-01", "gate": "G0", "kind": "fixture"},
            {"id": "LIST-01", "gate": "G1", "kind": "fixture"},
            {"id": "MUTATIONS-01", "gate": "G8", "kind": "fixture"},
            {"id": "REAL-01", "gate": "G9", "kind": "real_capture"},
            {"id": "CHECKS-01", "gate": "G10", "kind": "fixture"},
        ],
        "mutations": [
            {"id": "M01", "change": "Restore omitted-attachment", "must_fail": "LIST-01"},
        ]
    }
    contract_file = tmp_path / "docs" / "research" / "outlet-repair-acceptance.json"
    contract_file.parent.mkdir(parents=True, exist_ok=True)
    with open(contract_file, "w") as f:
        json.dump(contract, f)

    def _make_receipt(case_id: str, gate_id: str, kind: str = "fixture", status: str = "passed"):
        return {
            "test_id": case_id,
            "gate_id": gate_id,
            "evidence_kind": kind,
            "source_hash": "mock_source_hash",
            "input_hashes": ["mock_input_hash"],
            "command_or_ui_action": "mock command",
            "started_at": "2026-09-20T14:00:00-07:00",
            "finished_at": "2026-09-20T14:00:01-07:00",
            "exit_status_or_assertion_status": status,
            "expected_behavior": "mock expected",
            "actual_behavior": "mock actual",
            "artifact_paths_and_hashes": {"file.py": "hash"},
            "review_identity": "self_review",
        }

    return tmp_path, contract, _make_receipt


def test_verify_01_fails_on_missing_skipped_or_failed_test(mock_repo):
    tmp_path, contract, make_receipt = mock_repo
    progress_file = tmp_path / "docs/progress/PROGRESS_MOFFETT_OUTLET_REPAIR.json"
    progress_file.parent.mkdir(parents=True, exist_ok=True)

    # Case A: Missing test ID
    progress_data = {
        "receipts": {
            "STATUS-01": make_receipt("STATUS-01", "G0"),
            # LIST-01 missing!
            "MUTATIONS-01": make_receipt("MUTATIONS-01", "G8"),
            "REAL-01": make_receipt("REAL-01", "G9", kind="real_capture"),
            "CHECKS-01": make_receipt("CHECKS-01", "G10"),
        },
        "mutation_receipts": {
            "M01": {"mutation_id": "M01", "status": "killed", "assertion_failed": True},
        }
    }
    with open(progress_file, "w") as f:
        json.dump(progress_data, f)

    res = verify_acceptance(tmp_path)
    assert not res["ok"]
    assert any("LIST-01" in err and "no receipt" in err for err in res["errors"])
    assert res["case_status"]["LIST-01"] == "missing"

    # Case B: Skipped test
    progress_data["receipts"]["LIST-01"] = make_receipt("LIST-01", "G1", status="skipped")
    with open(progress_file, "w") as f:
        json.dump(progress_data, f)

    res = verify_acceptance(tmp_path)
    assert not res["ok"]
    assert any("LIST-01" in err and "skipped" in err for err in res["errors"])
    assert res["case_status"]["LIST-01"] == "skipped"

    # Case C: Failed test
    progress_data["receipts"]["LIST-01"] = make_receipt("LIST-01", "G1", status="failed")
    with open(progress_file, "w") as f:
        json.dump(progress_data, f)

    res = verify_acceptance(tmp_path)
    assert not res["ok"]
    assert any("LIST-01" in err and "failed" in err for err in res["errors"])
    assert res["case_status"]["LIST-01"] == "failed"


def test_verify_02_fails_on_synthetic_artifact_for_real_capture_and_survived_mutation(mock_repo):
    tmp_path, contract, make_receipt = mock_repo
    progress_file = tmp_path / "docs/progress/PROGRESS_MOFFETT_OUTLET_REPAIR.json"
    progress_file.parent.mkdir(parents=True, exist_ok=True)

    # Case A: Synthetic artifact claimed for real_capture
    progress_data = {
        "receipts": {
            "STATUS-01": make_receipt("STATUS-01", "G0"),
            "LIST-01": make_receipt("LIST-01", "G1"),
            "MUTATIONS-01": make_receipt("MUTATIONS-01", "G8"),
            "REAL-01": make_receipt("REAL-01", "G9", kind="synthetic"), # Not real_capture!
            "CHECKS-01": make_receipt("CHECKS-01", "G10"),
        },
        "mutation_receipts": {
            "M01": {"mutation_id": "M01", "status": "killed", "assertion_failed": True},
        }
    }
    with open(progress_file, "w") as f:
        json.dump(progress_data, f)

    res = verify_acceptance(tmp_path)
    assert not res["ok"]
    assert any("REAL-01" in err and "requires real_capture" in err for err in res["errors"])
    assert res["case_status"]["REAL-01"] == "invalid_evidence"

    # Case B: Survived mutation
    progress_data["receipts"]["REAL-01"] = make_receipt("REAL-01", "G9", kind="real_capture")
    progress_data["mutation_receipts"]["M01"] = {"mutation_id": "M01", "status": "survived", "assertion_failed": False}
    with open(progress_file, "w") as f:
        json.dump(progress_data, f)

    res = verify_acceptance(tmp_path)
    assert not res["ok"]
    assert any("M01" in err and "survived" in err for err in res["errors"])
    assert res["mutation_status"]["M01"] == "survived"


def test_verify_03_emits_expected_terminal_status(mock_repo):
    tmp_path, contract, make_receipt = mock_repo
    progress_file = tmp_path / "docs/progress/PROGRESS_MOFFETT_OUTLET_REPAIR.json"
    progress_file.parent.mkdir(parents=True, exist_ok=True)

    # All gates pass
    progress_data = {
        "receipts": {
            "STATUS-01": make_receipt("STATUS-01", "G0"),
            "LIST-01": make_receipt("LIST-01", "G1"),
            "MUTATIONS-01": make_receipt("MUTATIONS-01", "G8"),
            "REAL-01": make_receipt("REAL-01", "G9", kind="real_capture"),
            "CHECKS-01": make_receipt("CHECKS-01", "G10"),
        },
        "mutation_receipts": {
            "M01": {"mutation_id": "M01", "status": "killed", "assertion_failed": True},
        }
    }
    with open(progress_file, "w") as f:
        json.dump(progress_data, f)

    res = verify_acceptance(tmp_path)
    assert res["ok"]
    assert res["progress_pct"] == 100.0
    assert res["terminal_status"] == "verified_web_feature"
