"""Benchmark evaluating spatial layout proposals against both hard constraints and precedent constraints.

This module evaluates layouts (from baseline, search, or fine-tuned neural models)
against the Standard Physics constraint suite and the newly introduced ADA Case Precedent Corpus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from standardphysics_contracts import Mat4, SceneGraph
from standardphysics_contracts.precedents import SpaceTypology

from standardphysics_agents.fix.constraints import violations as hard_constraint_violations
from standardphysics_agents.precedents import (
    PrecedentCompiler,
    check_precedent_constraints,
    load_precedents,
)


@dataclass
class PrecedentBenchmarkSummary:
    total_evaluations: int = 0
    parsed_moves_count: int = 0
    hard_constraint_passes: int = 0
    precedent_constraint_passes: int = 0
    dual_constraint_passes: int = 0
    precedent_violations_by_case: dict[str, int] = field(default_factory=dict)
    precedent_violations_by_rule: dict[str, int] = field(default_factory=dict)

    @property
    def hard_constraint_pass_rate(self) -> float:
        return round(self.hard_constraint_passes / self.total_evaluations, 4) if self.total_evaluations else 0.0

    @property
    def precedent_constraint_pass_rate(self) -> float:
        return round(self.precedent_constraint_passes / self.total_evaluations, 4) if self.total_evaluations else 0.0

    @property
    def dual_constraint_pass_rate(self) -> float:
        return round(self.dual_constraint_passes / self.total_evaluations, 4) if self.total_evaluations else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_evaluations": self.total_evaluations,
            "parsed_moves_count": self.parsed_moves_count,
            "hard_constraint_pass_rate": self.hard_constraint_pass_rate,
            "precedent_constraint_pass_rate": self.precedent_constraint_pass_rate,
            "dual_constraint_pass_rate": self.dual_constraint_pass_rate,
            "precedent_violations_by_case": self.precedent_violations_by_case,
            "precedent_violations_by_rule": self.precedent_violations_by_rule,
        }


def apply_moves(base_graph: SceneGraph, moves: list[dict[str, Any]]) -> SceneGraph:
    """Apply discrete (dx, dy, rotation_degrees) moves to scene nodes."""
    moves_by_id = {m["node_id"]: m for m in moves if "node_id" in m}
    new_nodes = []
    for node in base_graph.nodes:
        if node.id in moves_by_id:
            m = moves_by_id[node.id]
            dx = float(m.get("dx", 0.0))
            dy = float(m.get("dy", 0.0))
            # Create updated transform
            current_m = list(node.transform.m)
            current_m[3] += dx
            current_m[7] += dy
            updated_node = node.model_copy(
                update={"transform": Mat4(m=current_m)}
            )
            new_nodes.append(updated_node)
        else:
            new_nodes.append(node)
    return base_graph.model_copy(update={"nodes": new_nodes})


def evaluate_precedent_benchmark(
    cases: list[dict[str, Any]],
    precedents_path: Path | None = None,
) -> PrecedentBenchmarkSummary:
    """Run full benchmark evaluating candidate proposals against hard + precedent constraints."""
    directives = load_precedents(path=precedents_path, allow_unverified=True)
    compiler = PrecedentCompiler(directives)
    summary = PrecedentBenchmarkSummary()

    for item in cases:
        base_graph: SceneGraph = item["base_graph"]
        typology: SpaceTypology = item.get("typology", SpaceTypology.RESTAURANT_DINING)
        candidate_moves: list[dict[str, Any]] = item.get("moves", [])

        summary.total_evaluations += 1
        if candidate_moves:
            summary.parsed_moves_count += 1

        # 1. Apply moves
        candidate_graph = apply_moves(base_graph, candidate_moves)

        # 2. Check standard hard constraints
        hard_violations = hard_constraint_violations(base_graph, candidate_graph)
        hard_pass = len(hard_violations) == 0
        if hard_pass:
            summary.hard_constraint_passes += 1

        # 3. Check precedent directives
        matched = compiler.match(typology, base_graph.nodes)
        precedent_violations = check_precedent_constraints(base_graph, candidate_graph, matched)
        precedent_pass = len(precedent_violations) == 0
        if precedent_pass:
            summary.precedent_constraint_passes += 1

        # Record specific precedent violation frequencies
        for pv in precedent_violations:
            summary.precedent_violations_by_case[pv.landmark_citation] = (
                summary.precedent_violations_by_case.get(pv.landmark_citation, 0) + 1
            )
            summary.precedent_violations_by_rule[pv.rule_broken] = (
                summary.precedent_violations_by_rule.get(pv.rule_broken, 0) + 1
            )

        if hard_pass and precedent_pass:
            summary.dual_constraint_passes += 1

    return summary
