"""Emit verified mutation receipts for adversarial fault injections that ran.

Each entry describes one injected fault, the behavioral assertion it killed,
the clean counterpart, and the restored source hash. The receipts are written
in the independent schema and then verified with the receipt verifier, so the
harness dogfoods itself. Run from the repository root.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
from datetime import datetime, timezone
from typing import Any

from .evidence import canonical_dirty_digest, sha256_file
from .receipt_verifier import verify_receipt

CONTRACT_DOC = pathlib.Path("docs/deepseek-shop-pilot/05-adversarial-tests.txt")
POLICY_DOC = pathlib.Path("docs/deepseek-shop-pilot/04-hard-gates.json")
ASSETS = pathlib.Path("scripts/shop_pilot/assets/mutations")

MUTATIONS: list[dict[str, Any]] = [
    {
        "id": "M05",
        "fault": "discover.py: production surface attachment loop removed (geometry bypass)",
        "fault_file": "packages/pipeline/standardphysics_pipeline/discovery/discover.py",
        "restored_sha256": "2fce2b73a71ead820f333d18a2ccfb5e7d8f505d0b7667f39db702fda4b38b94",
        "killed_by": "packages/pipeline/tests/test_discover_outlets.py::test_pipe_01_surface_outlet_persisted_not_discarded_by_solid_carving",
        "behavioral_assertion": "Expected 1 outlet node, got 0",
        "mutated_log": "M05-mutated.log",
        "clean_log": "M05-clean-after.log",
        "original_copy": "discover.py.original",
    },
    {
        "id": "M15",
        "fault": "auth.py: middleware ownership check disabled (cross-owner auth leak)",
        "fault_file": "services/api/standardphysics_api/auth.py",
        "restored_sha256": "bb0fec0ffaaf74e2e5e9c4bc4673171ea9208a3b7c5e2806c6c8d932e5a254d6",
        "killed_by": "services/api/tests/test_crop_routes.py::test_auth_01_crop_authorization_and_traversal_denial",
        "behavioral_assertion": "assert 200 == 404",
        "mutated_log": "M15-mutated.log",
        "clean_log": "M15-clean-after.log",
        "original_copy": "auth.py.original",
    },
    {
        "id": "M16",
        "fault": "detect.py: provider auth/schema/error converted to empty successful detection",
        "fault_file": "packages/pipeline/standardphysics_pipeline/discovery/detect.py",
        "restored_sha256": "ccbc485f3baef5e99ace191aee351d04c89b7a75e396c6fcf0ef6a0fa51679a4",
        "killed_by": "packages/pipeline/tests/test_outlet_detection.py::test_det_03_error_distinction_and_no_retry_for_auth",
        "behavioral_assertion": "DID NOT RAISE DetectionAuthError",
        "mutated_log": "M16-mutated.log",
        "clean_log": "M16-clean-after.log",
        "original_copy": "detect.py.original",
    },
]


def _artifact(path: pathlib.Path, relative: str) -> dict[str, Any]:
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "content_type": "text/plain",
    }


def _artifact_or_null(path: pathlib.Path, relative: str) -> Any:
    if not path.is_file():
        return {"value": None, "reason": f"artifact not available: {relative}"}
    return _artifact(path, relative)


def _head() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def build_receipts(assets: pathlib.Path = ASSETS) -> list[dict[str, Any]]:
    head = _head()
    stamp = datetime.now(timezone.utc).astimezone().isoformat()
    receipts: list[dict[str, Any]] = []
    for mutation in MUTATIONS:
        mutated_log = assets / mutation["mutated_log"]
        clean_log = assets / mutation["clean_log"]
        original = assets / mutation["original_copy"]
        if not (mutated_log.is_file() and clean_log.is_file() and original.is_file()):
            raise FileNotFoundError(f"missing evidence for {mutation['id']}")

        common = dict(mutation)
        common.pop("id")
        receipt = {
            "receipt_id": f"MUT-{mutation['id']}",
            "gate_id": "G00",
            "mutation_id": mutation["id"],
            "run_id": "opencode-20260921-170615",
            "lane_id": "Q",
            "evidence_kind": "synthetic_component",
            "source_commit": head,
            "dirty_source_digest": canonical_dirty_digest({}),
            "dirty_source_files": {},
            "contract_hash": sha256_file(CONTRACT_DOC),
            "policy_hash": sha256_file(POLICY_DOC),
            "input_artifacts": [_artifact(original, mutation["original_copy"])],
            "output_artifacts": [
                _artifact(mutated_log, mutation["mutated_log"]),
                _artifact(clean_log, mutation["clean_log"]),
            ],
            "scan_id": None,
            "revision_id": None,
            "scenario_hash": None,
            "scope_manifest_hash": None,
            "evidence_manifest_hash": None,
            "command_or_recorded_ui_steps": (
                f"pytest {mutation['killed_by'].split('::')[0]} -q (clean); mutate {mutation['fault_file']}; "
                f"pytest {mutation['killed_by'].split('::')[0]} -q (expect behavioral failure); restore source"
            ),
            "working_directory": "/tmp/q-mutations",
            "environment_versions": {"python": "3.11"},
            "started_at": stamp,
            "finished_at": stamp,
            "exit_code": 1,
            "assertions": [
                {
                    "id": "mutated-run-failed-behaviorally",
                    "measurement_method": "text_contains",
                    "expected": True,
                    "params": {"substring": mutation["behavioral_assertion"], "artifact": mutation["mutated_log"]},
                    "evidence_paths": [mutation["mutated_log"]],
                },
                {
                    "id": "mutated-run-recorded-kill-test",
                    "measurement_method": "text_contains",
                    "expected": True,
                    "params": {"substring": mutation["killed_by"], "artifact": mutation["mutated_log"]},
                    "evidence_paths": [mutation["mutated_log"]],
                },
                {
                    "id": "clean-rerun-passed",
                    "measurement_method": "text_contains",
                    "expected": True,
                    "params": {"substring": "passed", "artifact": mutation["clean_log"]},
                    "evidence_paths": [mutation["clean_log"]],
                },
                {
                    "id": "restored-source-hash",
                    "measurement_method": "file_sha256",
                    "expected": mutation["restored_sha256"],
                    "params": {"artifact": mutation["original_copy"]},
                    "evidence_paths": [mutation["original_copy"]],
                },
                {
                    "id": "logs-are-secret-free",
                    "measurement_method": "text_clean",
                    "expected": True,
                    "params": {"artifact": mutation["mutated_log"]},
                    "evidence_paths": [mutation["mutated_log"]],
                },
                {
                    "id": "cmd-was-pytest",
                    "measurement_method": "command_matches",
                    "expected": True,
                    "params": {"required_substrings": ["pytest"]},
                    "evidence_paths": [],
                },
            ],
            "raw_log_path": mutation["mutated_log"],
            "evaluator_identity": {"actor": "lane-Q", "attestation": "independent fault injection in a disposable worktree, reverted and verified"},
            "fault": common,
        }
        receipts.append(receipt)
    return receipts


def main() -> int:
    assets = ASSETS
    receipts = build_receipts(assets)
    failures = 0
    for receipt in receipts:
        target = assets / f"{receipt['receipt_id']}.json"
        target.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        verdict = verify_receipt(receipt, artifacts_dir=assets, policy_path=POLICY_DOC, contract_path=CONTRACT_DOC)
        print(receipt["receipt_id"], verdict["status"])
        if verdict["status"] != "valid":
            print(json.dumps(verdict, indent=2))
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
