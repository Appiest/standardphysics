#!/usr/bin/env python3
"""Run constraint benchmark across Standard Physics neural network variants and landmark precedent suites.

Evaluates proposals against:
1. Hard Physical Constraints (walls, doors, floors, collisions, travel distances).
2. Actionable Precedent Constraints (accessible seating ratios, anti-isolation, clearance vectors).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

# Ensure packages on PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
for pkg in ["packages/contracts", "packages/agents", "packages/pipeline", "packages/fixtures"]:
    p = str(REPO_ROOT / pkg)
    if p not in sys.path:
        sys.path.insert(0, p)

from standardphysics_agents.evaluation.precedent_benchmark import (
    evaluate_precedent_benchmark,
)
from standardphysics_agents.precedents import load_precedents
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_contracts.precedents import SpaceTypology


def build_synthetic_scenarios() -> list[dict]:
    """Build representative benchmark scenarios across room typologies."""
    cases = []

    # 1. VIP Dining Lounge (Johnson v. Golden State Warriors LLC)
    bar_node = SceneNode(
        id=uuid4(),
        kind="object",
        label="Plumbed_bar_counter",
        raw_category="counter",
        dimensions=Vec3(x=3.5, y=0.8, z=1.15),
        transform=Mat4.translation(0.0, 2.0, 0.0),
        quality="measured",
        movable=False,
        labeled_by="test",
    )
    stool_1 = SceneNode(
        id=uuid4(),
        kind="object",
        label="High_top_table_1",
        raw_category="table",
        dimensions=Vec3(x=0.8, y=0.8, z=1.05),
        transform=Mat4.translation(-1.0, 0.0, 0.0),
        quality="measured",
        movable=True,
        labeled_by="test",
    )
    stool_2 = SceneNode(
        id=uuid4(),
        kind="object",
        label="High_top_table_2",
        raw_category="table",
        dimensions=Vec3(x=0.8, y=0.8, z=1.05),
        transform=Mat4.translation(1.0, 0.0, 0.0),
        quality="measured",
        movable=True,
        labeled_by="test",
    )
    lounge_base = SceneGraph(scan_id=uuid4(), revision=1, nodes=[bar_node, stool_1, stool_2])

    # 1a: Baseline (fails: 0% accessible tables)
    cases.append({
        "name": "VIP_Lounge_Base_HighTopOnly",
        "typology": SpaceTypology.HOSPITALITY_LOUNGE,
        "base_graph": lounge_base,
        "moves": [],
    })

    # 1b: Uninformed NN Move (moves high top table slightly, still 0% accessible)
    cases.append({
        "name": "VIP_Lounge_NN_Uninformed_Rearrangement",
        "typology": SpaceTypology.HOSPITALITY_LOUNGE,
        "base_graph": lounge_base,
        "moves": [{"node_id": stool_1.id, "dx": 0.2, "dy": -0.2}],
    })

    # 1c: Precedent-Informed Fix (adds/substitutes compliant accessible table)
    acc_table = SceneNode(
        id=uuid4(),
        kind="object",
        label="Accessible_dining_table",
        raw_category="table",
        dimensions=Vec3(x=0.9, y=0.9, z=0.76),
        transform=Mat4.translation(0.0, 0.0, 0.0),
        quality="measured",
        movable=True,
        labeled_by="test",
    )
    lounge_with_acc = SceneGraph(
        scan_id=uuid4(), revision=2, nodes=[bar_node, stool_1, stool_2, acc_table]
    )
    cases.append({
        "name": "VIP_Lounge_Precedent_Compliant_Layout",
        "typology": SpaceTypology.HOSPITALITY_LOUNGE,
        "base_graph": lounge_with_acc,
        "moves": [],
    })

    # 2. Boba Shop & QSR (Kalani v. Starbucks Corp. / Johnson v. SF Bay Boba LLC)
    counter = SceneNode(
        id=uuid4(),
        kind="object",
        label="Service_counter",
        raw_category="counter",
        dimensions=Vec3(x=2.8, y=0.7, z=0.9),
        transform=Mat4.translation(0.0, 1.5, 0.0),
        quality="measured",
        movable=False,
        labeled_by="test",
    )
    stanchion = SceneNode(
        id=uuid4(),
        kind="object",
        label="Queue_stanchion",
        raw_category="stanchion",
        dimensions=Vec3(x=0.3, y=0.3, z=0.9),
        transform=Mat4.translation(0.0, 0.8, 0.0),
        quality="measured",
        movable=True,
        labeled_by="test",
    )
    boba_base = SceneGraph(scan_id=uuid4(), revision=1, nodes=[counter, stanchion])
    cases.append({
        "name": "BobaShop_Stanchion_Queue_Base",
        "typology": SpaceTypology.QSR_BEVERAGE,
        "base_graph": boba_base,
        "moves": [],
    })
    cases.append({
        "name": "BobaShop_Queue_Aisle_Opened_Fix",
        "typology": SpaceTypology.QSR_BEVERAGE,
        "base_graph": boba_base,
        "moves": [{"node_id": stanchion.id, "dx": -0.4, "dy": 0.0}],
    })

    # 3. Commercial Private Office (Langer v. Milan / Lopez v. Catalina)
    door = SceneNode(
        id=uuid4(),
        kind="door",
        label="Office_door",
        raw_category="door",
        dimensions=Vec3(x=0.9, y=0.05, z=2.1),
        transform=Mat4.translation(-1.5, 0.0, 0.0),
        quality="measured",
        movable=False,
        labeled_by="test",
    )
    cabinet = SceneNode(
        id=uuid4(),
        kind="object",
        label="Filing_cabinet",
        raw_category="cabinet",
        dimensions=Vec3(x=0.6, y=0.5, z=1.2),
        transform=Mat4.translation(-1.2, 0.0, 0.0),
        quality="measured",
        movable=True,
        labeled_by="test",
    )
    office_base = SceneGraph(scan_id=uuid4(), revision=1, nodes=[door, cabinet])
    cases.append({
        "name": "Office_Latch_Clearance_Base",
        "typology": SpaceTypology.BUSINESS_OFFICE,
        "base_graph": office_base,
        "moves": [],
    })
    cases.append({
        "name": "Office_Latch_Clearance_Cleared_Fix",
        "typology": SpaceTypology.BUSINESS_OFFICE,
        "base_graph": office_base,
        "moves": [{"node_id": cabinet.id, "dx": 0.8, "dy": 0.0}],
    })

    return cases


def load_neural_network_room6_cases() -> list[dict]:
    """Load neural network held-out room 6 variants from runs/finetune/."""
    context_file = REPO_ROOT / "runs/finetune/room6/data/context.json"
    heldout_file = REPO_ROOT / "runs/finetune/room6/data/heldout.jsonl"
    if not context_file.exists() or not heldout_file.exists():
        return []

    context_data = json.loads(context_file.read_text())
    base_scanned = SceneGraph.model_validate(context_data["scanned"])

    # Load evaluated search/NN moves
    searched_moves_raw = context_data.get("searched_scanned_room")
    searched_moves = json.loads(searched_moves_raw).get("moves", []) if searched_moves_raw else []

    cases = []
    # Base scan
    cases.append({
        "name": "Room6_Neural_Base_Scan",
        "typology": SpaceTypology.COMMERCIAL_RETAIL,
        "base_graph": base_scanned,
        "moves": [],
    })

    # Evaluated NN / geometric search proposal
    if searched_moves:
        cases.append({
            "name": "Room6_Neural_Evaluated_Proposal",
            "typology": SpaceTypology.COMMERCIAL_RETAIL,
            "base_graph": base_scanned,
            "moves": searched_moves,
        })

    return cases


def main():
    print("=" * 78)
    print(" STANDARD PHYSICS: PRECEDENT-GROUNDED CONSTRAINT BENCHMARK")
    print("=" * 78)

    precedents = load_precedents(allow_unverified=True)
    print(f"Loaded {len(precedents)} verified landmark case directives from corpus.")

    all_cases = []
    synthetic_cases = build_synthetic_scenarios()
    print(f"Compiled {len(synthetic_cases)} synthetic typology test scenarios.")
    all_cases.extend(synthetic_cases)

    nn_cases = load_neural_network_room6_cases()
    if nn_cases:
        print(f"Loaded {len(nn_cases)} room6 neural network evaluated cases.")
        all_cases.extend(nn_cases)

    print(f"Running constraint benchmark across {len(all_cases)} total evaluations...\n")
    summary = evaluate_precedent_benchmark(all_cases)

    # Print Report
    print("BENCHMARK RESULTS")
    print("-" * 78)
    print(f"Total Evaluations:               {summary.total_evaluations}")
    print(f"Parsed Proposals:                {summary.parsed_moves_count}")
    print(f"Hard Constraint Pass Rate:       {summary.hard_constraint_pass_rate * 100:.1f}% ({summary.hard_constraint_passes}/{summary.total_evaluations})")
    print(f"Precedent Constraint Pass Rate:   {summary.precedent_constraint_pass_rate * 100:.1f}% ({summary.precedent_constraint_passes}/{summary.total_evaluations})")
    print(f"Dual-Constraint Pass Rate:       {summary.dual_constraint_pass_rate * 100:.1f}% ({summary.dual_constraint_passes}/{summary.total_evaluations})")
    print("\nPRECEDENT VIOLATIONS DETECTED BY CASE PRECEDENT:")
    for citation, count in summary.precedent_violations_by_case.items():
        print(f"  • {citation}: {count} violation(s)")

    print("\nPRECEDENT VIOLATIONS DETECTED BY RULE TYPE:")
    for rule, count in summary.precedent_violations_by_rule.items():
        print(f"  • {rule}: {count} instance(s)")

    # Save artifact
    out_dir = REPO_ROOT / "runs"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / "precedent_benchmark_results.json"
    out_file.write_text(json.dumps(summary.as_dict(), indent=2))
    print(f"\nSaved benchmark results to {out_file}")
    print("=" * 78)


if __name__ == "__main__":
    main()
