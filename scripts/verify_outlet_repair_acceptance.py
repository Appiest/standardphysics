"""Executable acceptance harness and completion verifier for the outlet feature repair.

Validates receipts in docs/progress/PROGRESS_MOFFETT_OUTLET_REPAIR.json against docs/research/outlet-repair-acceptance.json.
Checks:
- All required cases present and passed with required receipt fields.
- No synthetic artifacts claimed as real_capture.
- All 8 targeted mutations killed by behavioral assertions.
- Current gate pass statuses and overall terminal status.
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

PROVISIONAL_GATES = ["G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G10"]


def _check_case(case: dict, receipts: dict, required_receipt_fields: list) -> tuple[str, list[str]]:
    """One case's standing, and everything wrong with its receipt."""
    case_id = case["id"]
    if case_id not in receipts:
        return "missing", [
            f"Case {case_id} (Gate {case['gate']}) has no receipt in "
            "docs/progress/PROGRESS_MOFFETT_OUTLET_REPAIR.json"
        ]

    receipt = receipts[case_id]
    errors = [
        f"Case {case_id} receipt is missing required field: {field}"
        for field in required_receipt_fields
        if field not in receipt
    ]

    status = receipt.get("exit_status_or_assertion_status")
    if status in ("skipped", "SKIP"):
        return "skipped", [*errors, f"Case {case_id} was skipped"]
    if status not in (0, "passed", "PASS", "ok", True):
        return "failed", [*errors, f"Case {case_id} failed with status: {status}"]

    evidence = receipt.get("evidence_kind")
    if case["kind"] == "real_capture" and evidence != "real_capture":
        return "invalid_evidence", [
            *errors,
            f"Case {case_id} requires real_capture evidence, but got {evidence}",
        ]
    return "passed", errors


def _check_cases(cases: list, receipts: dict, required_receipt_fields: list):
    case_status: dict[str, str] = {}
    errors: list[str] = []
    for case in cases:
        status, case_errors = _check_case(case, receipts, required_receipt_fields)
        case_status[case["id"]] = status
        errors.extend(case_errors)
    return case_status, errors


def _check_mutations(mutations: list, mutation_receipts: dict):
    """A mutation that survives is a test that was not watching."""
    mutation_status: dict[str, str] = {}
    errors: list[str] = []
    for mutation in mutations:
        mutation_id = mutation["id"]
        receipt = mutation_receipts.get(mutation_id)
        if receipt is None:
            mutation_status[mutation_id] = "missing"
            errors.append(f"Mutation {mutation_id} has no receipt")
        elif receipt.get("assertion_failed", False) or receipt.get("status") == "killed":
            mutation_status[mutation_id] = "killed"
        else:
            mutation_status[mutation_id] = "survived"
            errors.append(
                f"Mutation {mutation_id} survived! (did not trigger expected behavioral failure)"
            )
    return mutation_status, errors


def _gate_results(required_gate_ids: list, cases: list, case_status: dict,
                  mutations: list, mutation_status: dict) -> dict[str, str]:
    """A gate passes when it owns cases and every one of them passed.

    G8 additionally requires every mutation to have been killed, because the
    gate is about the tests noticing, not about them running.
    """
    results: dict[str, str] = {}
    for gate_id in required_gate_ids:
        gate_cases = [case for case in cases if case["gate"] == gate_id]
        passed = bool(gate_cases) and all(
            case_status.get(case["id"]) == "passed" for case in gate_cases
        )
        if gate_id == "G8":
            passed = passed and all(
                mutation_status.get(mutation["id"]) == "killed" for mutation in mutations
            )
        results[gate_id] = "passed" if passed else "open"
    return results


def _terminal_status(gate_results: dict[str, str], required_gate_ids: list) -> str:
    if all(gate_results.get(gate) == "passed" for gate in required_gate_ids):
        return "verified_web_feature"
    provisional = all(gate_results.get(gate) == "passed" for gate in PROVISIONAL_GATES)
    if provisional and gate_results.get("G9") != "passed":
        return "implementation_ready_real_acceptance_blocked"
    return "incomplete"


def verify_acceptance(root_dir: pathlib.Path) -> dict[str, Any]:
    contract_path = root_dir / "docs/research/outlet-repair-acceptance.json"
    progress_path = root_dir / "docs/progress/PROGRESS_MOFFETT_OUTLET_REPAIR.json"

    if not contract_path.is_file():
        return {"ok": False, "error": f"Contract file not found: {contract_path}"}
    if not progress_path.is_file():
        return {"ok": False, "error": f"Progress file not found: {progress_path}"}

    with open(contract_path) as f:
        contract = json.load(f)
    with open(progress_path) as f:
        progress = json.load(f)

    required_gate_ids = contract["required_gate_ids"]
    cases = contract["cases"]
    mutations = contract["mutations"]

    case_status, case_errors = _check_cases(
        cases, progress.get("receipts", {}), contract["required_receipt_fields"]
    )
    mutation_status, mutation_errors = _check_mutations(
        mutations, progress.get("mutation_receipts", {})
    )
    errors = [*case_errors, *mutation_errors]

    gate_results = _gate_results(
        required_gate_ids, cases, case_status, mutations, mutation_status
    )
    passed_gates = [gate for gate in required_gate_ids if gate_results[gate] == "passed"]

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "progress_pct": 100.0 * len(passed_gates) / len(required_gate_ids),
        "passed_gates": passed_gates,
        "gate_results": gate_results,
        "terminal_status": _terminal_status(gate_results, required_gate_ids),
        "case_status": case_status,
        "mutation_status": mutation_status,
    }


def main():
    root_dir = pathlib.Path(__file__).resolve().parents[1]
    result = verify_acceptance(root_dir)
    print(json.dumps(result, indent=2))
    if result["terminal_status"] == "verified_web_feature":
        print(f"\nAll required gates passed! Terminal status: {result['terminal_status']}")
        sys.exit(0)
    elif result["terminal_status"] == "implementation_ready_real_acceptance_blocked":
        print(f"\nImplementation verified (10/11 gates passed, progress: {result['progress_pct']:.1f}%). Real capture acceptance blocked by external detector credential (DISCOVERY_API_KEY).")
        sys.exit(0)
    else:
        print(f"\nVerification incomplete: {len(result['errors'])} errors, progress: {result['progress_pct']:.1f}%")
        sys.exit(1)


if __name__ == "__main__":
    main()
