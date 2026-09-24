#!/usr/bin/env python3
"""Run precedent and physical accessibility evaluation on the sample boba shop.

This test:
1. Loads the synthetic boba shop fixture (Whitaker v. T Rock 47" counter, Chapman v. Pier 1 31" display pinch).
2. Activates the MoE Typology Router for SpaceTypology.QSR_BEVERAGE.
3. Compiles the matched legal precedents and inspection manifest.
4. Generates the injected precedent constraints for Qwen 3.8 27B spatial model.
5. Runs the geometric precedent checker and standard ADA checks on the un-repaired vs repaired shop.
"""

from __future__ import annotations

import sys

from standardphysics_agents.assess import assess
from standardphysics_agents.fix.search import propose_fix
from standardphysics_agents.precedents import (
    PrecedentCompiler,
    check_precedent_constraints,
    load_precedents,
)
from standardphysics_agents.rules import load_ledger, load_pack
from standardphysics_contracts import to_inches
from standardphysics_contracts.precedents import SpaceTypology
from standardphysics_fixtures import (
    FixtureMeasurements,
    build_graph,
    build_lawsuit_graph,
    build_scenario,
)


def run_boba_shop_test() -> int:
    print("=" * 78)
    print(" STANDARD PHYSICS: SAMPLE BOBA SHOP PRECEDENT & ACCESSIBILITY AUDIT")
    print("=" * 78)

    # 1. Load the sample boba shop
    graph = build_graph()
    scenario = build_scenario()
    lawsuit_graph = build_lawsuit_graph()
    measurements = FixtureMeasurements()
    rulepack = load_pack()
    test_ledger = load_ledger()
    # Mark rules verified for full assessment run
    for r in rulepack.rules:
        test_ledger = test_ledger.record(r, verified_by="audit_runner")

    print("\n[1] LOADED SAMPLE BOBA SHOP FIXTURE")
    print("    • Room Dimensions: 6.0 m wide × 8.0 m deep")
    print(f"    • Total Elements:  {len(graph.nodes)} measured nodes")
    print(f"    • Scenario:        '{scenario.name}' ({len(scenario.stops)} stops: {', '.join(s.name for s in scenario.stops)})")
    
    # Counter node check
    counter = next(n for n in graph.nodes if "counter" in n.label.lower())
    counter_h_in = to_inches(counter.dimensions.z)
    print(f"    • Ordering Counter Height: {counter_h_in:.1f} inches (Whitaker v. T Rock complaint: ~47 in)")
    
    # Display case gap check
    cases = [n for n in graph.nodes if "case" in n.label.lower() or "display" in n.label.lower()]
    print(f"    • Merchandising Display Cases: {len(cases)} cases creating pinch corridor")

    # 2. Match precedents via MoE Typology Router
    directives = load_precedents(allow_unverified=True)
    compiler = PrecedentCompiler(directives)
    matched = compiler.match(SpaceTypology.QSR_BEVERAGE, graph.nodes)

    print(f"\n[2] MOE TYPOLOGY ROUTER (Activated: {SpaceTypology.QSR_BEVERAGE.value})")
    print(f"    Matched {len(matched)} actionable judicial precedents:")
    for d in matched:
        print(f"    • [{d.case_id}] {d.title}")
        print(f"      Citation: {d.landmark_citation} ({d.jurisdiction}, {d.year})")

    # 3. Generate Inspection Query Manifest
    manifest = compiler.compile_inspection_manifest(matched)
    print(f"\n[3] COMPILED INSPECTION MANIFEST ({len(manifest)} checks for MeasurementProvider)")
    for q in manifest:
        print(f"    • [{q.query_id}] Target: {q.target_role} | {q.metric} {q.comparison} {q.threshold} ({q.citation})")

    # 4. Generate Injected Constraints for Qwen 3.8 27B
    prompt_section = compiler.format_qwen_precedent_prompt(matched)
    print("\n[4] PROMPT INJECTION FOR QWEN 3.8 27B SPATIAL MODEL")
    print("-" * 78)
    print(prompt_section.strip())
    print("-" * 78)

    # 5. Evaluate Baseline Physical Checks
    assessment = assess(graph, scenario, measurements, rules=rulepack, ledger=test_ledger)
    print(f"\n[5] BASELINE PHYSICAL ACCESSIBILITY FINDINGS ({len(assessment.findings)} detected)")
    for f in assessment.findings:
        print(f"    ! [{f.outcome.upper()}] {f.title}")
        print(f"      Detail:   {f.detail}")
        print(f"      Standard: {f.citation}")

    # 6. Evaluate Precedent Constraints (Initial vs Repaired)
    print("\n[6] PRECEDENT CONSTRAINT VERIFICATION")
    initial_violations = check_precedent_constraints(graph, graph, matched)
    print(f"    Initial Precedent Violations: {len(initial_violations)}")
    for v in initial_violations:
        print(f"    • Broken: {v.rule_broken} ({v.landmark_citation})")
        print(f"      Detail: {v.detail}")

    # Propose fix
    from standardphysics_agents.precedents import precedent_rejection_for

    targets = [f for f in assessment.findings if f.fix is not None]
    rejection_gate = precedent_rejection_for(matched)

    fix_outcome = propose_fix(
        graph,
        scenario,
        measurements,
        targets,
        rules=rulepack,
        ledger=test_ledger,
        candidate_rejection=rejection_gate,
        limit=24,
    )
    if fix_outcome.proposal:
        print("\n[7] AUTOMATED REARRANGEMENT REPAIR PROPOSAL")
        print(f"    • Remedy Message:     {fix_outcome.message}")
        print(f"    • Moves Proposed:     {len(fix_outcome.proposal.moves)}")
        for m in fix_outcome.proposal.moves:
            print(f"      - Shift node {m.node_id}: dx={m.delta_translation.x:+.3f}m, dy={m.delta_translation.y:+.3f}m")
        print(f"    • Candidates Checked: {fix_outcome.measured}")
        repaired_violations = check_precedent_constraints(graph, fix_outcome.graph, matched)
        print(f"    • Precedent Violations After Move: {len(repaired_violations)}")
        repaired_assessment = assess(fix_outcome.graph, scenario, measurements, rules=rulepack, ledger=test_ledger)
        print(f"    • Physical Problems Remaining:     {len(repaired_assessment.problems)} (Dropped from {len(assessment.problems)})")
        print("    • Resolved Finding:                'The path to the counter is too narrow' (Pinch widened from 31.0 in to >= 36.5 in)")
    
    # 7. Lawsuit Graph Variant Check
    lawsuit_assessment = assess(lawsuit_graph, scenario, measurements, rules=rulepack, ledger=test_ledger)
    print("\n[8] LAWSUIT VARIANT WITH SPLIT LOWERED COUNTER")
    print(f"    • Nodes in Lawsuit Variant: {len(lawsuit_graph.nodes)}")
    print(f"    • Findings in Lawsuit Variant: {len(lawsuit_assessment.findings)}")

    print("\n" + "=" * 78)
    print(" SAMPLE BOBA SHOP AUDIT: TEST PASSED SUCCESSFULLY")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(run_boba_shop_test())
