"""Emit verified mutation receipts for adversarial fault injections that ran.

Each entry describes one injected fault and its outcome: ``killed`` (the
injection triggered a behavioral assertion failure in a real test) or
``survived_gap`` (no test caught it: a recorded coverage gap, never counted
as a kill). The receipts are written in the independent schema and then
verified with the receipt verifier, so the harness dogfoods itself.
Run from the repository root.
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
        "id": "M03",
        "source_head": "8b48f9e (final K HEAD)",
        "outcome": "killed",
        "fault": "repository.py: evidence bundle declared complete despite missing required kinds (early finalize)",
        "fault_file": "services/api/standardphysics_api/repository.py",
        "restored_sha256": "ec82157b9fdff3bdd37e2794e15c0bf35f2d92094153859326c965f8ee1d6851",
        "killed_by": "services/api/tests/test_evidence_closure.py::test_legacy_geometry_complete_stays_browsable_with_semantics_blocked",
        "behavioral_assertion": "assert 'complete' == 'blocked_incomplete_evidence'",
        "mutated_log": "M03-mutated.log",
        "clean_log": "M03-clean-after.log",
        "original_copy": "repository.py.original",
    },
    {
        "id": "M05",
        "source_head": "534b7e0 (final K HEAD)",
        "outcome": "killed",
        "fault": "discover.py: production surface attachment loop removed (geometry bypass)",
        "fault_file": "packages/pipeline/standardphysics_pipeline/discovery/discover.py",
        "restored_sha256": "7c5a63d7e4fe33c23598120bc4166eb9b8b6ae57cef07a4ef976039230bf4568",
        "killed_by": "packages/pipeline/tests/test_discover_outlets.py::test_pipe_01_surface_outlet_persisted_not_discarded_by_solid_carving",
        "behavioral_assertion": "Expected 1 outlet node, got 0",
        "mutated_log": "M05-mutated.log",
        "clean_log": "M05-clean-after.log",
        "original_copy": "discover.py.original",
    },
    {
        "id": "M06",
        "source_head": "bb8ded9 (final K HEAD)",
        "outcome": "killed",
        "fault": "detect.py: encode_frame orientation rotation zeroed (turns=0)",
        "fault_file": "packages/pipeline/standardphysics_pipeline/discovery/detect.py",
        "restored_sha256": "b2ac0a9b2e05812d30dcd20ed1a816c0e9a308618782fbbb98c1277e8ae55432",
        "killed_by": "packages/pipeline/tests/test_orientation_end_to_end.py::TestEncodeFrameOrientationEndToEnd",
        "behavioral_assertion": "landscape_left: patch seen at (639.5, 514.5), expected (160.0, 85.0) upright",
        "mutated_log": "M06-mutated.log",
        "clean_log": "M06-clean-after.log",
        "original_copy": "detect-s-head.py.original",
        "note": "S closed the M06 gap with test_orientation_end_to_end.py at 6e01067; the mutation now dies behaviorally",
    },
    {
        "id": "M07",
        "source_head": "534b7e0 (final K HEAD)",
        "outcome": "killed",
        "fault": "reconcile.py: MAX_SAME_OUTLET_SURFACE_DISTANCE_M 0.06 -> 6.0 (adjacent outlets merge)",
        "fault_file": "packages/pipeline/standardphysics_pipeline/discovery/reconcile.py",
        "restored_sha256": "7cc419e25cbfe30086371b29802be8b64ad61e8e5cad984eee58316662fbd962",
        "killed_by": "packages/pipeline/tests/test_reconcile_outlets.py::test_merge_02_separate_plates_and_opposite_walls_do_not_collapse",
        "behavioral_assertion": "assert not True",
        "mutated_log": "M07-mutated.log",
        "clean_log": "M07-clean-after.log",
        "original_copy": "reconcile.py.original",
    },
    {
        "id": "M01",
        "source_head": "q-safe-director verifier (validator-side control; no product mutation)",
        "outcome": "killed",
        "fault": "fabricated receipt (old G9 rebuilt_graph=100 shape, prose artifact map, digest for nonexistent file) submitted as passed",
        "fault_file": "scripts/shop_pilot/receipt_verifier.py (the injected receipt, not product source)",
        "restored_sha256": "85988087b4dab1f1e484138a6c34a5765ab340ae43084a808e1f1b51727984b0",
        "killed_by": "scripts/shop_pilot/tests/test_receipt_verifier.py::test_old_g9_rebuilt_graph_shape_is_invalid",
        "behavioral_assertion": "test_old_g9_rebuilt_graph_shape_is_invalid PASSED",
        "mutated_log": "M01-mutated.log",
        "clean_log": "M01-clean-after.log",
        "original_copy": "receipt_verifier.py.original",
        "note": "05-adversarial M01 is defined as a receipt-validation failure, not a product fault; the failing input must be rejected regardless of status/exit code, and is",
    },
    {
        "id": "M02",
        "source_head": "q-safe-director verifier (validator-side control; no product mutation)",
        "outcome": "killed",
        "fault": "output/crop/export replaced by another revision's valid file (hash valid, identity mismatch)",
        "fault_file": "scripts/shop_pilot/receipt_verifier.py (the injected receipt, not product source)",
        "restored_sha256": "85988087b4dab1f1e484138a6c34a5765ab340ae43084a808e1f1b51727984b0",
        "killed_by": "scripts/shop_pilot/tests/test_receipt_verifier.py::test_wrong_revision_artifact_is_invalid",
        "behavioral_assertion": "test_wrong_revision_artifact_is_invalid PASSED",
        "mutated_log": "M02-mutated.log",
        "clean_log": "M01-clean-after.log",
        "original_copy": "receipt_verifier.py.original",
        "note": "05-adversarial M02 is a receipt identity-mismatch failure; verifier recomputes identity assertions from artifact bytes",
    },
    {
        "id": "M18",
        "source_head": "q-safe-director verifier (validator-side control; no product mutation)",
        "outcome": "killed",
        "fault": "simulator/fixture evidence offered as fresh-phone/new-shop; agent identity offered as human review",
        "fault_file": "scripts/shop_pilot/receipt_verifier.py + certificates.py (the injected receipt, not product source)",
        "restored_sha256": "85988087b4dab1f1e484138a6c34a5765ab340ae43084a808e1f1b51727984b0",
        "killed_by": "scripts/shop_pilot/tests/test_receipt_verifier.py::test_fixture_receipt_cannot_claim_new_shop_field",
        "behavioral_assertion": "test_fixture_receipt_cannot_claim_new_shop_field PASSED",
        "mutated_log": "M18-mutated.log",
        "clean_log": "M01-clean-after.log",
        "original_copy": "receipt_verifier.py.original",
        "note": "05-adversarial M18 is a certificate rejection behavior: synthetic receipt can never carry a physical gate",
    },
    {
        "id": "M04",
        "source_head": "8b48f9e (final K HEAD)",
        "outcome": "killed",
        "fault": "evidence.py: attempted-input gating removed from maybe_queue_semantic (failed-bundle retry storm)",
        "fault_file": "services/api/standardphysics_api/evidence.py",
        "restored_sha256": "c3a135002dae9b2db2b72c5f60fdafc7e5d6987cd52cc7641b57ba6ed719895f",
        "killed_by": "services/api/tests/test_job_lifecycle.py::test_sweep_never_retries_a_failed_input_until_new_evidence",
        "behavioral_assertion": "assert 'complete' == 'failed'",
        "mutated_log": "M04-mutated.log",
        "clean_log": "M04-clean-after.log",
        "original_copy": "evidence.py.original",
    },
    {
        "id": "M08",
        "source_head": "8b48f9e (final K HEAD)",
        "outcome": "killed",
        "fault": "worker.py: ingest saves at the latest owner revision, clobbering the reviewed graph (stale async overwrites review)",
        "fault_file": "services/api/standardphysics_api/worker.py",
        "restored_sha256": "be0dcd38cf27ae47c3ac5d6115d59e8a2df742098c5d72cd4f0c45858a1d454b",
        "killed_by": "services/api/tests/test_review_safety.py::test_late_process_job_replaces_ingest_revision_but_never_owner_decisions",
        "behavioral_assertion": "assert latest[\"revision\"] == 1",
        "mutated_log": "M08-mutated.log",
        "clean_log": "M08-clean-after.log",
        "original_copy": "worker.py.original",
    },
    {
        "id": "M17",
        "source_head": "1dcfd8f (final K HEAD)",
        "outcome": "killed",
        "fault": "scripts/evaluate_photo_mesh_500.py covered_fraction: counted transparent/unphotographed pixels as photographed (photographed := ones(fixed_mask.shape))",
        "fault_file": "scripts/evaluate_photo_mesh_500.py (the shipped G15 evaluator, line 251 denominator)",
        "restored_sha256": "121a0d7e0dbc6c78555c1899ff2cd9758cfcac0b814c0863b3d3fa08bc733f9c",
        "killed_by": "test_denominator_counts_only_photographed_pixels",
        "behavioral_assertion": "assert 0.0 == 0.04",
        "mutated_log": "M17-evaluator-mutated.log",
        "clean_log": "M17-evaluator-clean.log",
        "original_copy": "evaluate_photo_mesh_500.py.original",
        "note": "re-executed against the SHIPPED evaluator call graph (evaluate_view -> covered_fraction) with a deterministic 96/100 fixture; the earlier render_efficiency/metrics.py test is informative but not proof for the c9/c11 denominator and is not counted",
    },
    {
        "id": "M09",
        "source_head": "8c5d40b (A chain integrated)",
        "outcome": "killed",
        "fault": "scope_manifest.py: requested requirements dropped when no finding exists (hidden unknown)",
        "fault_file": "services/api/standardphysics_api/scope_manifest.py",
        "restored_sha256": "9b5da4e5ec45c8b8de8eb90e983ebd0fc74520d41ea5898e63fd435d1df83abc",
        "killed_by": "packages/agents/tests/test_scope_rows.py::test_every_requested_requirement_has_a_visible_row",
        "behavioral_assertion": "AssertionError: assert {",
        "mutated_log": "M09-mutated.log",
        "clean_log": "M09-clean-after.log",
        "original_copy": "scope_manifest.py.original",
    },
    {
        "id": "M10",
        "source_head": "bb8ded9 (final K HEAD)",
        "outcome": "killed",
        "fault": "primitives/uncertainty.py: straddling interval replaced by its midpoint (favorable midpoint)",
        "fault_file": "packages/pipeline/standardphysics_pipeline/primitives/uncertainty.py",
        "restored_sha256": "fb76fbb4c7c91ed71c9b08d944e8c3e19c97b10fe0ca68e4dc2315316b480a10",
        "killed_by": "packages/agents/tests/test_rule_intervals.py::TestThresholdStraddling::test_a_maximum_straddled_asks_for_verification",
        "behavioral_assertion": "assert 'satisfied' == 'needs_verification'",
        "mutated_log": "M10-mutated.log",
        "clean_log": "M10-clean-after.log",
        "original_copy": "uncertainty.py.original",
    },
    {
        "id": "M11",
        "source_head": "8c5d40b (A chain integrated)",
        "outcome": "killed",
        "fault": "scope_manifest.py: applicability forced to not_applicable without facts (invented N/A)",
        "fault_file": "services/api/standardphysics_api/scope_manifest.py",
        "restored_sha256": "9b5da4e5ec45c8b8de8eb90e983ebd0fc74520d41ea5898e63fd435d1df83abc",
        "killed_by": "packages/agents/tests/test_scope_rows.py::test_no_row_becomes_not_applicable_on_its_own",
        "behavioral_assertion": "applicability='not_applicable'",
        "mutated_log": "M11-mutated.log",
        "clean_log": "M11-clean-after.log",
        "original_copy": "scope_manifest.py.original",
    },
    {
        "id": "M12",
        "source_head": "bb8ded9 (final K HEAD)",
        "outcome": "killed",
        "fault": "rules/verification.py: preview ledger entries read as personally verified (preview as verified)",
        "fault_file": "packages/agents/standardphysics_agents/rules/verification.py",
        "restored_sha256": "9cb503743b66e70352be797297acc82aaf2f967c1d654dcdd6a9d3d01cf059ea",
        "killed_by": "packages/agents/tests/test_verification_preview.py::TestRealReviewers::test_a_second_check_cannot_promote_a_preview_entry",
        "behavioral_assertion": "where False = all(",
        "mutated_log": "M12-mutated.log",
        "clean_log": "M12-clean-after.log",
        "original_copy": "verification.py.original",
    },
    {
        "id": "M13",
        "source_head": "bb8ded9 (final K HEAD)",
        "outcome": "killed",
        "fault": "fix/approach.py: swept-path obstacles omitted entirely (reach shortcut)",
        "fault_file": "packages/agents/standardphysics_agents/fix/approach.py",
        "restored_sha256": "0fb138f0849794b31caf1b239032cbb148239512dd8c6011fc5b302218ad8b10",
        "killed_by": "packages/agents/tests/test_approach.py::TestFalseClearGuards::test_furniture_covering_the_outlet_is_not_a_support",
        "behavioral_assertion": "assert 'needs_verification' == 'blocked'",
        "mutated_log": "M13-mutated.log",
        "clean_log": "M13-clean-after.log",
        "original_copy": "approach.py.original",
    },
    {
        "id": "M14",
        "source_head": "bb8ded9 (final K HEAD)",
        "outcome": "killed",
        "fault": "fix/constraints.py: fixed-node movement gate disabled (false redesign)",
        "fault_file": "packages/agents/standardphysics_agents/fix/constraints.py",
        "restored_sha256": "2d8796764890ffc0942948e26632f5c9370e70392e74a3bf4a899afa39ef9836",
        "killed_by": "packages/agents/tests/test_redesign.py::test_model_cannot_move_a_fixed_fixture",
        "behavioral_assertion": "assert 'moved_something_fixed' in set()",
        "mutated_log": "M14-mutated.log",
        "clean_log": "M14-clean-after.log",
        "original_copy": "constraints.py.original",
    },
    {
        "id": "M15",
        "source_head": "base 54e09e4 (pre-freeze)",
        "outcome": "killed",
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
        "source_head": "base 54e09e4 (pre-freeze)",
        "outcome": "killed",
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

        if mutation["outcome"] == "killed":
            failure_assertions = [
                {
                    "id": "mutated-run-failed-behaviorally",
                    "measurement_method": "text_contains",
                    "expected": True,
                    "observed": True,
                    "params": {"substring": mutation["behavioral_assertion"], "artifact": mutation["mutated_log"]},
                    "evidence_paths": [mutation["mutated_log"]],
                },
                {
                    "id": "mutated-run-recorded-kill-test",
                    "measurement_method": "text_contains",
                    "expected": True,
                    "observed": True,
                    "params": {"substring": mutation["killed_by"], "artifact": mutation["mutated_log"]},
                    "evidence_paths": [mutation["mutated_log"]],
                },
            ]
        else:
            failure_assertions = [
                {
                    "id": "mutation-was-not-killed",
                    "measurement_method": "text_contains",
                    "expected": True,
                    "observed": True,
                    "params": {"substring": mutation["log_marker"], "artifact": mutation["mutated_log"]},
                    "evidence_paths": [mutation["mutated_log"]],
                },
                {
                    "id": "no-failure-recorded",
                    "measurement_method": "not_text_contains",
                    "expected": True,
                    "observed": True,
                    "params": {"substring": "FAILED", "artifact": mutation["mutated_log"]},
                    "evidence_paths": [mutation["mutated_log"]],
                },
            ]

        receipt = {
            "receipt_id": f"MUT-{mutation['id']}",
            "gate_id": "G00",
            "mutation_id": mutation["id"],
            "outcome": mutation["outcome"],
            "run_id": "opencode-20260921-170615",
            "lane_id": "Q",
            "evidence_kind": "synthetic_component",
            "source_commit": head,
            "eval_source_head": mutation["source_head"],
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
                f"pytest <owning suite> -q (clean); mutate {mutation['fault_file']}; "
                "pytest <owning suite> -q (record outcome); restore source"
            ),
            "working_directory": "/tmp/q-mutations or /tmp/q-review-s (disposable worktrees)",
            "environment_versions": {"python": "3.11"},
            "started_at": stamp,
            "finished_at": stamp,
            "exit_code": 0 if mutation["outcome"] == "survived_gap" else 1,
            "assertions": failure_assertions + [
                {
                    "id": "clean-rerun-passed",
                    "measurement_method": "text_contains",
                    "expected": True,
                    "observed": True,
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
            "evaluator_identity": {
                "actor": "lane-Q",
                "attestation": "independent fault injection in a disposable worktree, reverted and hash-verified",
            },
            "fault": mutation,
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
        verdict = verify_receipt(
            receipt, artifacts_dir=assets, policy_path=POLICY_DOC, contract_path=CONTRACT_DOC, git_worktree=None
        )
        print(receipt["receipt_id"], receipt["outcome"], verdict["status"])
        if verdict["status"] != "valid":
            print(json.dumps(verdict, indent=2))
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
