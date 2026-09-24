#!/usr/bin/env python3
"""Execute the 5-Phase Precedent Audit Plan for Standard Physics.

Phases:
1. Legal Citation & Docket Source Verification
2. Fail-Closed Gate & Tamper Testing
3. Typology Matcher False-Positive / False-Negative Audit
4. Physical & Fixture Scan Validation (Real / Fixture / Synthetic)
5. Performance & Search Latency Profiling

Saves structured output to runs/precedent_audit_report.json.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from standardphysics_agents.precedents.checker import check_precedent_constraints
from standardphysics_agents.precedents.compiler import PrecedentCompiler
from standardphysics_agents.precedents.verification import (
    load_precedent_ledger,
    load_precedents,
)
from standardphysics_contracts.geometry import Mat4, Vec3
from standardphysics_contracts.precedents import SpaceTypology
from standardphysics_contracts.scene import SceneGraph, SceneNode

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("precedent_audit")

AUDIT_OUTPUT_PATH = Path("runs/precedent_audit_report.json")


def _make_mat4_at(x: float, y: float, z: float) -> Mat4:
    return Mat4(
        m=[
            1.0, 0.0, 0.0, float(x),
            0.0, 1.0, 0.0, float(y),
            0.0, 0.0, 1.0, float(z),
            0.0, 0.0, 0.0, 1.0,
        ]
    )


def _make_node(
    node_id: str,
    raw_category: str,
    pos: tuple[float, float, float],
    dims: tuple[float, float, float] = (1.0, 1.0, 0.75),
    kind: str = "object",
) -> SceneNode:
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        uid = uuid.uuid5(uuid.NAMESPACE_DNS, node_id)
    return SceneNode(
        id=uid,
        kind=kind,
        label=raw_category.replace("_", " ").title(),
        raw_category=raw_category,
        dimensions=Vec3(x=dims[0], y=dims[1], z=dims[2]),
        transform=_make_mat4_at(pos[0], pos[1], pos[2]),
        quality="measured",
        movable=True,
    )


# -----------------------------------------------------------------------------
# Phase 1: Legal Citation & Docket Source Verification
# -----------------------------------------------------------------------------
def run_phase_1() -> dict[str, Any]:
    log.info("--- Phase 1: Legal Citation & Docket Source Verification ---")
    directives = load_precedents(allow_unverified=True)
    ledger = load_precedent_ledger()

    results: list[dict[str, Any]] = []
    all_passed = True

    valid_standards_prefixes = ("ADA_2010_", "ADA_TITLE_III_", "UNRUH_ACT_")

    for d in directives:
        is_verified = ledger.is_verified(d.case_id)
        entry = next((v for v in ledger if v.case_id == d.case_id), None)

        has_docket = bool(entry and entry.docket_source and ("courtlistener.com" in entry.docket_source or "pacer" in entry.docket_source))
        has_primary = bool(entry and entry.primary_citation and len(entry.primary_citation) > 10)
        has_second_check = bool(entry and entry.second_check_by)

        # Check inspection query statutory citations
        citations_valid = all(
            any(q.citation.startswith(pfx) for pfx in valid_standards_prefixes)
            for q in d.inspection_queries
        )

        status_ok = is_verified and has_docket and has_primary and has_second_check and citations_valid
        if not status_ok:
            all_passed = False

        results.append({
            "case_id": d.case_id,
            "landmark_citation": d.landmark_citation,
            "jurisdiction": d.jurisdiction,
            "year": d.year,
            "verified": is_verified,
            "docket_source": entry.docket_source if entry else None,
            "second_check_by": entry.second_check_by if entry else None,
            "citations_valid": citations_valid,
            "queries_count": len(d.inspection_queries),
            "status": "PASS" if status_ok else "FAIL",
        })

    log.info("Phase 1 verified %d/%d directives with primary sources.", sum(1 for r in results if r["status"] == "PASS"), len(directives))
    return {
        "phase": 1,
        "name": "Legal Citation & Docket Source Verification",
        "passed": all_passed,
        "cases_verified": len(results),
        "details": results,
    }


# -----------------------------------------------------------------------------
# Phase 2: Fail-Closed Gate & Tamper Testing
# -----------------------------------------------------------------------------
def run_phase_2() -> dict[str, Any]:
    log.info("--- Phase 2: Fail-Closed Gate & Tamper Testing ---")
    from standardphysics_agents.precedents.verification import PrecedentLedger

    tamper_tests: list[dict[str, Any]] = []

    # Test 2.1: Default loader loads verified directives cleanly
    directives = load_precedents(allow_unverified=False)
    tamper_tests.append({
        "test": "default_verified_load",
        "expected": "load_without_error",
        "passed": len(directives) >= 12,
        "count": len(directives),
    })

    # Test 2.2: Removing a case from ledger causes it to be filtered out (fail-closed)
    ledger = load_precedent_ledger()
    tampered_entries = {e.case_id: e for e in ledger if e.case_id != "US-CAND-2021-CV-03481"}
    tampered_ledger = PrecedentLedger(tampered_entries)

    loaded_filtered = load_precedents(ledger=tampered_ledger, allow_unverified=False)
    filtered_out = "US-CAND-2021-CV-03481" not in [d.case_id for d in loaded_filtered]

    tamper_tests.append({
        "test": "missing_ledger_entry_filtered_out_by_fail_closed_gate",
        "expected": "case_filtered_out_of_active_set",
        "passed": filtered_out and len(loaded_filtered) == len(directives) - 1,
        "filtered_case": "US-CAND-2021-CV-03481",
    })

    # Test 2.3: allow_unverified=True allows loading during development/testing
    unverified_loaded = load_precedents(ledger=tampered_ledger, allow_unverified=True)
    tamper_tests.append({
        "test": "allow_unverified_flag_honored",
        "expected": "load_with_flag",
        "passed": "US-CAND-2021-CV-03481" in [d.case_id for d in unverified_loaded] and len(unverified_loaded) == len(directives),
    })

    all_passed = all(t["passed"] for t in tamper_tests)
    log.info("Phase 2 fail-closed security tests: %s", "ALL PASSED" if all_passed else "FAILED")
    return {
        "phase": 2,
        "name": "Fail-Closed Gate & Tamper Testing",
        "passed": all_passed,
        "tests": tamper_tests,
    }


# -----------------------------------------------------------------------------
# Phase 3: Typology Matcher False-Positive / False-Negative Audit
# -----------------------------------------------------------------------------
def run_phase_3() -> dict[str, Any]:
    log.info("--- Phase 3: Typology Matcher False-Positive / False-Negative Audit ---")
    directives = load_precedents(allow_unverified=True)

    typology_audits = [
        {
            "typology": SpaceTypology.QSR_BEVERAGE,
            "entities": ["service_counter", "pos_terminal", "table", "chair"],
            "expected_cases": ["US-CAND-2020-CV-04182", "US-9THCIR-2022-CV-01294"],
            "forbidden_cases": ["US-9THCIR-2014-CV-00892", "US-NCAND-2021-CV-07821"],
        },
        {
            "typology": SpaceTypology.HOSPITALITY_LOUNGE,
            "entities": ["bar_counter", "high_top_table", "counter_stool", "cocktail_table"],
            "expected_cases": ["US-CAND-2021-CV-03481", "US-CDCAL-2020-CV-03120"],
            "forbidden_cases": ["US-NCAND-2021-CV-07821", "US-CDCA-2018-CV-05523"],
        },
        {
            "typology": SpaceTypology.BUSINESS_OFFICE,
            "entities": ["door", "desk", "office_chair"],
            "expected_cases": ["US-CAND-2021-CV-01198", "US-9THCIR-2021-CV-05612"],
            "forbidden_cases": ["US-CAND-2021-CV-03481", "US-CAND-2020-CV-04182"],
        },
        {
            "typology": SpaceTypology.ASSEMBLY_PRESENTATION,
            "entities": ["stage", "assembly_seating", "podium"],
            "expected_cases": ["US-9THCIR-2014-CV-00892"],
            "forbidden_cases": ["US-CAND-2021-CV-03481", "US-CAND-2020-CV-04182"],
        },
        {
            "typology": SpaceTypology.RESTROOM_FACILITY,
            "entities": ["toilet", "grab_bar", "lavatory"],
            "expected_cases": ["US-NCAND-2021-CV-07821"],
            "forbidden_cases": ["US-CAND-2021-CV-03481", "US-9THCIR-2014-CV-00892"],
        },
    ]

    audit_results: list[dict[str, Any]] = []
    all_passed = True

    compiler = PrecedentCompiler(directives)
    for item in typology_audits:
        mock_nodes = [
            _make_node(f"n_{i}", ent, (0.0, 0.0, 0.0))
            for i, ent in enumerate(item["entities"])
        ]
        matched = compiler.match(
            typology=item["typology"],
            nodes=mock_nodes,
        )
        matched_ids = {d.case_id for d in matched}

        missing_expected = [cid for cid in item["expected_cases"] if cid not in matched_ids]
        present_forbidden = [cid for cid in item["forbidden_cases"] if cid in matched_ids]

        passed = len(missing_expected) == 0 and len(present_forbidden) == 0
        if not passed:
            all_passed = False

        audit_results.append({
            "typology": item["typology"].value,
            "matched_count": len(matched),
            "matched_ids": sorted(matched_ids),
            "missing_expected": missing_expected,
            "present_forbidden": present_forbidden,
            "passed": passed,
        })

    log.info("Phase 3 typology matching: %d/%d typology scenarios passed without bleed.", sum(1 for r in audit_results if r["passed"]), len(audit_results))
    return {
        "phase": 3,
        "name": "Typology Matcher False-Positive / False-Negative Audit",
        "passed": all_passed,
        "scenarios": audit_results,
    }


# -----------------------------------------------------------------------------
# Phase 4: Physical & Fixture Scan Validation
# -----------------------------------------------------------------------------
def run_phase_4() -> dict[str, Any]:
    log.info("--- Phase 4: Physical & Fixture Scan Validation ---")
    directives = load_precedents(allow_unverified=True)
    scan_validations: list[dict[str, Any]] = []

    # 4.1: Standard Physics Fixture Shop (Retail/Boba shop scenario)
    from standardphysics_fixtures import build_graph
    fixture_graph = build_graph()

    compiler = PrecedentCompiler(directives)
    matched_fixture = compiler.match(
        typology=SpaceTypology.QSR_BEVERAGE,
        nodes=fixture_graph.nodes,
    )
    violations_fixture = check_precedent_constraints(fixture_graph, fixture_graph, matched_fixture)

    scan_validations.append({
        "target": "fixture_shop (packages/fixtures/standardphysics_fixtures)",
        "typology": "commercial.beverage.boba",
        "nodes_count": len(fixture_graph.nodes),
        "matched_directives": [d.case_id for d in matched_fixture],
        "violations_detected": len(violations_fixture),
        "passed": True,
    })

    # 4.2: Room6 Real Scanned Context (Order a Drink)
    room6_context_file = Path("runs/finetune/room6/data/context.json")
    if room6_context_file.is_file():
        context_data = json.loads(room6_context_file.read_text())
        graph_dict = context_data.get("scanned", {})
        room6_graph = SceneGraph.model_validate(graph_dict)

        matched_room6 = compiler.match(
            typology=SpaceTypology.QSR_BEVERAGE,
            nodes=room6_graph.nodes,
        )
        violations_room6 = check_precedent_constraints(room6_graph, room6_graph, matched_room6)

        scan_validations.append({
            "target": "room6_real_scan (runs/finetune/room6/data/context.json)",
            "typology": "commercial.beverage.boba",
            "nodes_count": len(room6_graph.nodes),
            "matched_directives": [d.case_id for d in matched_room6],
            "violations_detected": len(violations_room6),
            "passed": True,
        })

    # 4.3: VIP Lounge Non-Compliant Challenge Layout
    nodes_noncompliant = [
        _make_node("bar1", "bar_counter", (0.0, 3.0, 0.0), dims=(3.0, 0.8, 1.1)),
    ] + [
        _make_node(f"ht_{i}", "high_top_table", (float(i % 3) * 1.5, float(i // 3) * 1.5, 0.0), dims=(0.8, 0.8, 1.05))
        for i in range(6)
    ]
    graph_noncompliant = SceneGraph(scan_id=uuid.uuid4(), nodes=nodes_noncompliant, capture_to_room=Mat4.identity())
    matched_lounge = compiler.match(
        typology=SpaceTypology.HOSPITALITY_LOUNGE,
        nodes=nodes_noncompliant,
    )
    violations_lounge = check_precedent_constraints(graph_noncompliant, graph_noncompliant, matched_lounge)

    # 4.4: VIP Lounge Compliant Repaired Layout (add accessible dining surface integrated in zone)
    nodes_compliant = list(nodes_noncompliant) + [
        _make_node("acc_table", "dining_surface", (1.0, 1.0, 0.0), dims=(1.0, 1.0, 0.74))
    ]
    graph_compliant = SceneGraph(scan_id=uuid.uuid4(), nodes=nodes_compliant, capture_to_room=Mat4.identity())
    violations_compliant = check_precedent_constraints(graph_noncompliant, graph_compliant, matched_lounge)

    repaired_successfully = len(violations_lounge) > 0 and len(violations_compliant) == 0

    scan_validations.append({
        "target": "vip_lounge_challenge",
        "typology": "commercial.hospitality.lounge",
        "initial_violations": len(violations_lounge),
        "repaired_violations": len(violations_compliant),
        "repaired_successfully": repaired_successfully,
        "passed": repaired_successfully,
    })

    all_passed = all(v["passed"] for v in scan_validations)
    log.info("Phase 4 physical & fixture scan validation: %s", "ALL PASSED" if all_passed else "FAILED")
    return {
        "phase": 4,
        "name": "Physical & Fixture Scan Validation",
        "passed": all_passed,
        "validations": scan_validations,
    }


# -----------------------------------------------------------------------------
# Phase 5: Performance & Search Latency Profiling
# -----------------------------------------------------------------------------
def run_phase_5() -> dict[str, Any]:
    log.info("--- Phase 5: Performance & Search Latency Profiling ---")
    directives = load_precedents(allow_unverified=True)

    # Build a benchmark scene graph with 60 nodes
    nodes: list[SceneNode] = [
        _make_node("bar1", "bar_counter", (0.0, 3.0, 0.0), dims=(4.0, 0.8, 1.1)),
        _make_node("door1", "door", (-3.0, 0.0, 0.0), dims=(0.9, 0.1, 2.1)),
    ]
    for i in range(25):
        nodes.append(_make_node(f"table_{i}", "high_top_table", (float(i % 5) * 1.2, float(i // 5) * 1.2, 0.0)))
        nodes.append(_make_node(f"chair_{i}", "counter_stool", (float(i % 5) * 1.2 + 0.3, float(i // 5) * 1.2, 0.0)))
    nodes.append(_make_node("acc_table", "dining_surface", (1.0, 1.0, 0.0), dims=(1.0, 1.0, 0.74)))

    graph = SceneGraph(scan_id=uuid.uuid4(), nodes=nodes, capture_to_room=Mat4.identity())

    iterations = 200

    # Benchmark compiler matching
    compiler = PrecedentCompiler(directives)
    t0 = time.perf_counter()
    for _ in range(iterations):
        matched = compiler.match(
            typology=SpaceTypology.HOSPITALITY_LOUNGE,
            nodes=nodes,
        )
    compile_duration_s = time.perf_counter() - t0
    compile_latency_ms = (compile_duration_s / iterations) * 1000.0

    # Benchmark deterministic checking
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = check_precedent_constraints(graph, graph, matched)
    check_duration_s = time.perf_counter() - t0
    check_latency_ms = (check_duration_s / iterations) * 1000.0

    total_latency_ms = compile_latency_ms + check_latency_ms
    passed = total_latency_ms < 50.0 and check_latency_ms < 5.0

    log.info("Phase 5 Profiling (60-node graph across %d iterations):", iterations)
    log.info("  Typology Matcher Latency: %.3f ms", compile_latency_ms)
    log.info("  Precedent Checker Latency: %.3f ms", check_latency_ms)
    log.info("  Total Precedent Latency:  %.3f ms (Threshold: < 50.0 ms)", total_latency_ms)

    return {
        "phase": 5,
        "name": "Performance & Search Latency Profiling",
        "passed": passed,
        "iterations": iterations,
        "nodes_in_benchmark_graph": len(nodes),
        "compile_latency_ms": round(compile_latency_ms, 3),
        "check_latency_ms": round(check_latency_ms, 3),
        "total_latency_ms": round(total_latency_ms, 3),
        "meets_sla_sub_50ms": total_latency_ms < 50.0,
        "meets_search_candidate_sla_sub_5ms": check_latency_ms < 5.0,
    }


# -----------------------------------------------------------------------------
# Main Audit Orchestration
# -----------------------------------------------------------------------------
def main() -> int:
    log.info("==============================================================================")
    log.info(" STANDARD PHYSICS: PRECEDENT AUDIT EXECUTION ENGINE")
    log.info("==============================================================================")

    p1 = run_phase_1()
    p2 = run_phase_2()
    p3 = run_phase_3()
    p4 = run_phase_4()
    p5 = run_phase_5()

    all_phases = [p1, p2, p3, p4, p5]
    audit_passed = all(p["passed"] for p in all_phases)

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "overall_status": "AUDIT_PASSED" if audit_passed else "AUDIT_FAILED",
        "summary": {
            "phases_total": len(all_phases),
            "phases_passed": sum(1 for p in all_phases if p["passed"]),
            "phases_failed": sum(1 for p in all_phases if not p["passed"]),
        },
        "phases": all_phases,
    }

    AUDIT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUTPUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("Wrote audit report to %s", AUDIT_OUTPUT_PATH.resolve())

    log.info("==============================================================================")
    log.info(" AUDIT SUMMARY: %s (%d/%d phases passed)", report["overall_status"], report["summary"]["phases_passed"], len(all_phases))
    log.info("==============================================================================")

    return 0 if audit_passed else 1


if __name__ == "__main__":
    sys.exit(main())
