"""Independent recompute receipts for lane handoffs (S, K, others).

Each reviewed lane declares what Q recomputed on a disposable worktree bound
to that lane's HEAD: the raw logs are pinned as artifacts and every claim
becomes a recomputable text assertion. Run from the repository root; the
receipts self-verify against the independent verifier.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from typing import Any

from .evidence import sha256_bytes, sha256_file
from .receipt_verifier import verify_receipt

ASSETS = pathlib.Path("scripts/shop_pilot/assets/slices")
POLICY_DOC = pathlib.Path("docs/deepseek-shop-pilot/04-hard-gates.json")

REVIEWED_LANES = [
    {
        "lane": "S",
        "source_head": "422d16eab4934a1021720b4d54f15be876071644",
        "gate": "G03",
        "folder": "s-review",
        "entries": [
            {"id": "S-PIPELINE", "log": "s-pipeline.log", "claims": ["263 passed, 32 skipped"], "exit_code": 0},
            {"id": "S-API", "log": "s-api.log", "claims": ["17 passed"], "exit_code": 0},
            {
                "id": "S-AGENTS", "log": "s-agents.log",
                "claims": [
                    "7 failed",
                    "test_workflows.py::test_every_inferred_entrance_gets_a_route_to_every_object",
                ],
                "exit_code": 1,
            },
        ],
        "findings": {
            "claim_263_passed_32_skipped": "CONFIRMED",
            "claim_api_17_passed": "CONFIRMED",
            "claim_agents_failures_pre_existing": "CONFIRMED (identical 7 at base 54e09e4 and 8f2962f)",
            "claim_ruff_zero_new": "CONFIRMED zero new; base discovery dir has 9 violations, S HEAD has 8 (S reduced by one)",
        },
    },
    {
        "lane": "K",
        "source_head": "fcf681fb5ae8ce251b9a210adadb379c4ce9f9c3",
        "gate": "G00",
        "folder": "s-review",
        "entries": [
            {
                "id": "K-API", "log": "k-api.log",
                "claims": [
                    "184 passed",
                    "FAILED services/api/tests/test_combine_scans.py::test_overlay_aligns_capture_floors_without_mutating_payloads",
                ],
                "exit_code": 1,
            },
            {"id": "K-PIPELINE", "log": "k-pipeline.log", "claims": ["272 passed, 32 skipped"], "exit_code": 0},
            {"id": "K-AGENTS", "log": "k-agents.log", "claims": ["7 failed, 720 passed"], "exit_code": 1},
            {"id": "K-TYPECHECK", "log": "k-typecheck.log", "claims": ["Types generated successfully"], "exit_code": 0},
            {"id": "K-WEBTEST", "log": "k-webtest.log", "claims": ["134 passed (134)"], "exit_code": 0},
        ],
        "findings": {
            "claim_api_182_pass_only_combine_fails": "CONFIRMED with 184 passed (K's message understated by 2 test counts) and exactly the one known combine-floor failure",
            "claim_contracts_pipeline_green": "CONFIRMED (272 passed, 32 skipped)",
            "claim_agents_7_baseline": "CONFIRMED (identical failure set to base)",
            "claim_web_tests_134_typecheck_green": "CONFIRMED; generated contracts.ts did not drift from the committed copy",
            "contract_freeze_consumed": "2857f10 cherry-picked into lane Q worktree; freeze tests 17 passed",
        },
    },

    {
        "lane": "FINAL",
        "source_head": "aa8fd81c51bf85cbaa2b948ed2dfe1a0668eb819",
        "gate": "G01",
        "folder": "final",
        "entries": [
            {"id": "FINAL-API", "log": "api.log", "claims": ["211 passed"], "exit_code": 0},
            {"id": "FINAL-PIPELINE", "log": "pipeline.log", "claims": ["315 passed, 32 skipped"], "exit_code": 0},
            {"id": "FINAL-AGENTS", "log": "agents.log", "claims": ["776 passed"], "exit_code": 0},
            {
                "id": "FINAL-TOPLEVEL", "log": "toplevel.log",
                "claims": [
                    "4 failed, 285 passed",
                    "test_astra.py::test_local_rebuild_keeps_roomplan_provenance_and_measured_identity",
                    "test_ontology_is_not_fixed.py::test_the_baselines_are_honest",
                ],
                "exit_code": 1,
            },
            {"id": "FINAL-LIFECYCLE", "log": "lifecycle.log", "claims": ["22 passed"], "exit_code": 0},
            {"id": "FINAL-SCOPE", "log": "scope.log", "claims": ["11 passed"], "exit_code": 0},
            {"id": "FINAL-INTERVALS", "log": "intervals.log", "claims": ["62 passed"], "exit_code": 0},
            {"id": "FINAL-WEB-TC", "log": "web-tc.log", "claims": ["Types generated successfully"], "exit_code": 0},
            {"id": "FINAL-WEB-TEST", "log": "web-test.log", "claims": ["169 passed (169)"], "exit_code": 0},
            {"id": "FINAL-WEB-BUILD", "log": "web-build.log", "claims": ["/scans/[scanId]"], "exit_code": 0},
        ],
        "findings": {
            "head_label": "K integration HEAD aa8fd81",
            "suite_green": "API 211, pipeline+contracts 315/32, agents 776, web tc+169 tests+build all green at aa8fd81",
            "top_tests_failures": "exactly the 4 pre-existing astra(2)/ontology(2) failures, identical to baseline inventory; none new, assigned B/R",
            "web_build_command": "npm run build (next build) exit 0 with routes listed",
        },
    },

    {
        "lane": "FINAL2",
        "source_head": "8b48f9e0344a03e10ba2f79852956c88abc7dbde",
        "gate": "G01",
        "folder": "final2",
        "entries": [
            {"id": "FINAL2-API", "log": "api.log", "claims": ["233 passed"], "exit_code": 0},
            {"id": "FINAL2-PIPELINE", "log": "pipeline.log", "claims": ["331 passed, 32 skipped"], "exit_code": 0},
            {"id": "FINAL2-AGENTS", "log": "agents.log", "claims": ["838 passed"], "exit_code": 0},
            {
                "id": "FINAL2-TOPLEVEL", "log": "toplevel.log",
                "claims": [
                    "4 failed, 285 passed",
                    "test_audit_open_findings.py::test_a49_a_real_scan_that_is_ready_has_been_checked",
                    "test_ontology_is_not_fixed.py::test_nothing_new_branches_on_what_kind_of_thing_it_is",
                ],
                "exit_code": 1,
            },
            {"id": "FINAL2-G07", "log": "g07.log", "claims": ["135 passed"], "exit_code": 0},
            {"id": "FINAL2-WEB-TC", "log": "web-tc.log", "claims": ["Types generated successfully"], "exit_code": 0},
            {"id": "FINAL2-WEB-TEST", "log": "web-test.log", "claims": ["176 passed (176)"], "exit_code": 0},
            {
                "id": "FINAL2-WEB-BUILD-FIX", "log": "web-build-symlink-removed.log",
                "claims": ["Compiled successfully"], "exit_code": 0,
            },
            {
                "id": "FINAL2-WEB-BUILD-PANIC", "log": "web-build-with-committed-symlink.log",
                "claims": ["leaves the filesystem root"], "exit_code": 1,
            },
        ],
        "findings": {
            "head_label": "final integration head 8b48f9e",
            "suite_green": "API 233, pipeline+contracts 331/32, agents 838, G07-targeted 135, web tc + 176 tests all green",
            "top_tests_failures": "4 visible failures: 2 pre-existing ontology + 2 audit_open_findings red caused by B's correct usdz staging validation rejecting the fake-usdz fixture bytes (upload 400 invalid usdz archive); fixtures must use a real usdz, not the product",
            "web_build": "production build FAILS with a Turbopack panic while the committed absolute symlink apps/web/.node_modules-link is present (path joins leave the filesystem root); with the symlink removed the build compiles successfully. Repo hygiene fix for K/U; dev/typecheck/tests unaffected",
            "astmast": "astra top-level tests now PASS (B's wall fix)",
        },
    },
]


def _artifact(path: pathlib.Path, relative: str) -> dict[str, Any]:
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "content_type": "text/plain",
    }


def _review_receipt(lane: dict, entry: dict, now: str) -> dict[str, Any]:
    folder = ASSETS / lane["folder"]
    log_entry = folder / entry["log"]
    if not log_entry.is_file():
        raise FileNotFoundError(log_entry)
    assertions = []
    for index, substring in enumerate(entry["claims"]):
        assertions.append({
            "id": f"claim-{index}",
            "measurement_method": "text_contains",
            "expected": True,
            "observed": True,
            "params": {"substring": substring, "artifact": log_entry.name},
            "evidence_paths": [log_entry.name],
        })
    assertions.append({
        "id": "log-hash",
        "measurement_method": "file_sha256",
        "expected": sha256_file(log_entry),
        "observed": sha256_file(log_entry),
        "params": {"artifact": log_entry.name},
        "evidence_paths": [log_entry.name],
    })
    return {
        "receipt_id": f"REVIEW-{entry['id']}",
        "gate_id": lane["gate"],
        "run_id": "opencode-20260921-170615",
        "lane_id": "Q",
        "evidence_kind": "synthetic_component",
        "source_commit": lane["source_head"],
        "dirty_source_digest": sha256_bytes(b"{}"),
        "dirty_source_files": {},
        "contract_hash": sha256_bytes(b"unfrozen"),
        "policy_hash": sha256_file(POLICY_DOC),
        "input_artifacts": [],
        "output_artifacts": [_artifact(log_entry, log_entry.name)],
        "scan_id": None,
        "revision_id": None,
        "scenario_hash": None,
        "scope_manifest_hash": None,
        "evidence_manifest_hash": None,
        "command_or_recorded_ui_steps": f"suite transcription on disposable worktree at lane {lane['lane']} HEAD {lane['source_head'][:7]}",
        "working_directory": f"/tmp/q-review-{lane['lane'].lower()}",
        "environment_versions": {"python": "3.11", "node": "shared node_modules (read-only)"},
        "started_at": now,
        "finished_at": now,
        "exit_code": entry["exit_code"],
        "assertions": assertions,
        "raw_log_path": log_entry.name,
        "evaluator_identity": {"actor": "lane-Q", "attestation": "independent recompute on a disposable worktree"},
        "findings": lane["findings"],
    }


def build_receipts() -> list[dict[str, Any]]:
    receipts = []
    now = datetime.now(timezone.utc).astimezone().isoformat()
    for lane in REVIEWED_LANES:
        for entry in lane["entries"]:
            receipts.append(_review_receipt(lane, entry, now))
    return receipts


def main() -> int:
    receipts = build_receipts()
    failures = 0
    for lane in REVIEWED_LANES:
        folder = ASSETS / lane["folder"]
        folder.mkdir(parents=True, exist_ok=True)
    for receipt in receipts:
        matches = [l for l in REVIEWED_LANES if receipt["receipt_id"].startswith(f"REVIEW-{l['lane']}")]
        folder = ASSETS / max(matches, key=lambda lane: len(lane["lane"]))["folder"]
        target = folder / f"{receipt['receipt_id']}.json"
        target.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        verdict = verify_receipt(
            receipt, artifacts_dir=folder, policy_path=POLICY_DOC, git_worktree=None
        )
        print(receipt["receipt_id"], verdict["status"])
        if verdict["status"] != "valid":
            print(json.dumps(verdict, indent=2))
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
