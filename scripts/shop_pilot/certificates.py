"""Compute gate and certificate status from independently verified receipts.

A gate is only ``passed`` when a receipt for it is independently valid AND its
evidence class is strong enough for what the gate claims. Synthetic evidence
can prove software wiring (G01-G09) but can never carry a real-capture, fresh
phone, field, render or human-review gate. No self-asserted status is read.
"""

from __future__ import annotations

from typing import Any

from .receipt_verifier import STATUS_BLOCKED, STATUS_INVALID, STATUS_VALID, verify_receipt

REQUIRED_EVIDENCE: dict[str, tuple[str, ...]] = {
    "G00": ("synthetic_component",),
    "G01": ("synthetic_component", "synthetic_production_path"),
    "G02": ("synthetic_production_path",),
    "G03": ("synthetic_production_path",),
    "G04": ("synthetic_production_path",),
    "G05": ("synthetic_component", "synthetic_production_path"),
    "G06": ("synthetic_production_path",),
    "G07": ("synthetic_production_path",),
    "G08": ("synthetic_production_path",),
    "G09": ("synthetic_production_path",),
    "G10": ("saved_real_capture",),
    "G11": ("fresh_physical_phone",),
    "G12": ("saved_real_capture", "human_rule_review"),
    "G13": ("saved_real_capture",),
    "G14": ("human_rule_review",),
    "G15": ("actual_render", "actual_device_runtime"),
    "G16": ("new_shop_field",),
    "G17": ("human_control_measurement",),
}
CERTIFICATE_GATES: dict[str, tuple[str, ...]] = {
    "software_verified": ("G00", "G01", "G02", "G03", "G04", "G05", "G06", "G07", "G08", "G09"),
    "saved_capture_verified": ("G00", "G01", "G02", "G03", "G04", "G05", "G06", "G07", "G08", "G09", "G10"),
    "phone_pilot_ready": ("G00", "G01", "G02", "G03", "G04", "G05", "G06", "G07", "G08", "G09", "G11"),
    "shop_field_validated": ("G00", "G01", "G02", "G03", "G04", "G05", "G06", "G07", "G08", "G09", "G11", "G16"),
    "automatic_semantics_benchmark_passed": ("G00", "G02", "G03", "G10", "G12"),
    "scoped_assessment_evidence_ready": ("G00", "G06", "G08", "G10", "G13"),
    "scoped_requirements_satisfied": ("G00", "G05", "G06", "G13", "G14", "G17"),
    "photo_mesh_500_verified": ("G00", "G05", "G09", "G15"),
}
# Hard minimums from 04-hard-gates.json that receipts alone must meet.
# G00: 18 behavioral mutation kills (limits.mutations_required).
GATE_MINIMUMS: dict[str, tuple[tuple[str, int], ...]] = {
    "G00": (("mutation_receipts", 18),),
}


def _below_minimum(gate_id: str, verifications: list[dict[str, Any]]) -> str | None:
    for metric, minimum in GATE_MINIMUMS.get(gate_id, ()):
        if metric == "mutation_receipts":
            killed = sum(
                1
                for result in verifications
                if result["status"] == STATUS_VALID
                and result.get("mutation_id")
                and result.get("outcome") == "killed"
            )
            if killed < minimum:
                return f"only {killed} of {minimum} required behavioral mutation kills evidenced"
    return None


def _gate_outcome(gate_id: str, verifications: list[dict[str, Any]]) -> dict[str, Any]:
    allowed = REQUIRED_EVIDENCE.get(gate_id, ())
    passing = [r for r in verifications if r["evidence_kind"] in allowed and r["status"] == STATUS_VALID]
    if passing:
        shortfall = _below_minimum(gate_id, passing)
        if shortfall is not None:
            return {
                "id": gate_id, "status": "failed", "reason": shortfall,
                "receipt_ids": [r["receipt_id"] for r in passing],
            }
        return {"id": gate_id, "status": "passed", "receipt_ids": [r["receipt_id"] for r in passing]}
    invalid = [r for r in verifications if r["status"] == STATUS_INVALID]
    if invalid:
        return {
            "id": gate_id, "status": "failed",
            "reason": "receipt(s) failed independent verification",
            "receipt_ids": [r["receipt_id"] for r in invalid],
        }
    blocked = [r for r in verifications if r["status"] == STATUS_BLOCKED]
    if blocked:
        return {
            "id": gate_id, "status": "externally_blocked",
            "reason": "receipt needs physical/runtime identity evidence that is not available",
            "receipt_ids": [r["receipt_id"] for r in blocked],
        }
    return {
        "id": gate_id, "status": "pending",
        "reason": "no valid receipt of a sufficient evidence class",
        "receipt_ids": [r["receipt_id"] for r in verifications],
    }


def evaluate(
    receipts: list[dict[str, Any]],
    *,
    artifacts_dir: Any,
    policy_path: Any = None,
    contract_path: Any = None,
    git_worktree: Any = None,
) -> dict[str, Any]:
    verifications: list[dict[str, Any]] = []
    for receipt in receipts:
        verifications.append(
            verify_receipt(
                receipt,
                artifacts_dir=artifacts_dir,
                policy_path=policy_path,
                contract_path=contract_path,
                git_worktree=git_worktree,
            )
        )
    by_gate: dict[str, list[dict[str, Any]]] = {}
    for result in verifications:
        by_gate.setdefault(str(result.get("gate_id")), []).append(result)

    gates = [_gate_outcome(gate_id, by_gate.get(gate_id, [])) for gate_id in REQUIRED_EVIDENCE]
    status_by_id = {gate["id"]: gate["status"] for gate in gates}
    certificates = {
        name: {
            "status": "passed" if all(status_by_id.get(g) == "passed" for g in members) else "pending",
            "gates": list(members),
        }
        for name, members in CERTIFICATE_GATES.items()
    }
    return {"gates": gates, "certificates": certificates, "verifications": verifications}
