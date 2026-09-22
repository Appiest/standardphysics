"""Demonstrate the acceptance gap the old verifier leaves open.

The pre-existing verifier accepts a receipt whose only "evidence" is a prose
artifact map, a nonzero-looking status and no real bytes. This test pins that
gap as a regression: the old verifier says ok, the independent verifier
rejects the same receipt. It is the reason the new harness exists.
"""

from __future__ import annotations

import json
import pathlib

from scripts.shop_pilot.receipt_verifier import verify_receipt
from scripts.verify_outlet_repair_acceptance import verify_acceptance

FAKE_ARTIFACT_MAP = {"rebuilt_graph": "100"}


def _old_style_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    contract = {
        "required_gate_ids": ["G9"],
        "required_receipt_fields": [
            "test_id", "gate_id", "evidence_kind", "source_hash", "input_hashes",
            "command_or_ui_action", "started_at", "finished_at",
            "exit_status_or_assertion_status", "expected_behavior", "actual_behavior",
            "artifact_paths_and_hashes", "review_identity",
        ],
        "cases": [{"id": "REAL-01", "gate": "G9", "kind": "real_capture"}],
        "mutations": [],
    }
    contract_dir = tmp_path / "docs"
    contract_dir.mkdir(parents=True, exist_ok=True)
    (contract_dir / "outlet-repair-acceptance.json").write_text(json.dumps(contract), encoding="utf-8")
    receipt = {
        "test_id": "REAL-01",
        "gate_id": "G9",
        "evidence_kind": "real_capture",
        "source_hash": "self_reported_hash",
        "input_hashes": ["self_reported_input"],
        "command_or_ui_action": "collect real-acceptance artifacts and run receipts",
        "started_at": "2026-09-20T14:49:10-07:00",
        "finished_at": "2026-09-20T14:49:12-07:00",
        "exit_status_or_assertion_status": "passed",
        "expected_behavior": "actual detector persists an outlet",
        "actual_behavior": "Successfully passed using actual detector against real capture data.",
        "artifact_paths_and_hashes": FAKE_ARTIFACT_MAP,
        "review_identity": "self_review",
    }
    progress = {"receipts": {"REAL-01": receipt}, "mutation_receipts": {}}
    (tmp_path / "PROGRESS_MOFFETT_OUTLET_REPAIR.json").write_text(json.dumps(progress), encoding="utf-8")
    return tmp_path


def test_old_verifier_accepts_fabricated_receipt(tmp_path):
    root = _old_style_repo(tmp_path)
    legacy = verify_acceptance(root)
    assert legacy["ok"] is True
    assert legacy["case_status"]["REAL-01"] == "passed"


def test_new_verifier_rejects_same_fabricated_receipt(tmp_path):
    root = _old_style_repo(tmp_path)
    legacy_receipt = json.loads((root / "PROGRESS_MOFFETT_OUTLET_REPAIR.json").read_text())["receipts"]["REAL-01"]
    result = verify_receipt(legacy_receipt, artifacts_dir=root)
    assert result["status"] == "invalid"
    assert any("missing required field" in reason for reason in result["invalid_reasons"])
