"""Negative proofs for the certificate derivation: changing one gate
disposition blocks its descendants and their certificates without any
code edit to the derivation rules."""

from __future__ import annotations

import json
import pathlib

from scripts.shop_pilot.recompute_certificates import compute

POLICY = json.loads(
    (pathlib.Path("docs/deepseek-shop-pilot/04-hard-gates.json")).read_text(encoding="utf-8")
)

BASE_STATUSES = {
    gate["id"]: "passed"
    for gate in POLICY["gates"]
}


def _matrix(statuses: dict[str, str]) -> dict:
    return {"gate_matrix": [{"id": gate_id, "status": status} for gate_id, status in statuses.items()]}


def test_g09_failure_blocks_g10_and_g13_descendants():
    statuses = dict(BASE_STATUSES)
    statuses["G09"] = "externally_blocked"
    statuses["G10"] = "passed"
    statuses["G13"] = "passed"
    result = compute(_matrix(statuses), POLICY)
    certificates = result["certificates"]
    assert "G09" in certificates["software_verified"]["blocked_by"]
    assert "G09" in certificates["saved_capture_verified"]["blocked_by"]
    assert "G10" in certificates["saved_capture_verified"]["blocked_by"]
    assert "G13" in certificates["scoped_assessment_evidence_ready"]["blocked_by"]
    assert certificates["automatic_semantics_benchmark_passed"]["blocked_by"]


def test_unknown_gate_never_grants():
    statuses = dict(BASE_STATUSES)
    statuses["G05"] = "unknown"
    result = compute(_matrix(statuses), POLICY)
    certificates = result["certificates"]
    assert "G05" in certificates["software_verified"]["blocked_by"]
    assert "G05" in certificates["photo_mesh_500_verified"]["blocked_by"]


def test_all_passed_awards_software_certificate_only_when_real():
    result = compute(_matrix(dict(BASE_STATUSES)), POLICY)
    certificates = result["certificates"]
    assert certificates["software_verified"]["status"] == "awarded"


def test_physical_gates_block_real_certificates():
    statuses = dict(BASE_STATUSES)
    for gate_id in ("G11", "G16"):
        statuses[gate_id] = "externally_blocked"
    statuses["G14"] = "externally_blocked"
    statuses["G17"] = "externally_blocked"
    result = compute(_matrix(statuses), POLICY)
    certificates = result["certificates"]
    assert "G11" in certificates["phone_pilot_ready"]["blocked_by"]
    assert "G16" in certificates["shop_field_validated"]["blocked_by"]
    assert certificates["scoped_requirements_satisfied"]["blocked_by"]
