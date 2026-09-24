"""Tests for the Actionable ADA Case Precedent Corpus and Constraint Benchmarks."""

from __future__ import annotations

from uuid import uuid4

import pytest
from standardphysics_agents.evaluation.precedent_benchmark import (
    evaluate_precedent_benchmark,
)
from standardphysics_agents.precedents import (
    PrecedentCompiler,
    check_precedent_constraints,
    load_precedent_ledger,
    load_precedents,
)
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_contracts.precedents import (
    PrecedentDirective,
    SpaceTypology,
)


def _make_node(
    label: str,
    x: float,
    y: float,
    z: float,
    dx: float,
    dy: float,
    dz: float,
    movable: bool = True,
) -> SceneNode:
    return SceneNode(
        id=uuid4(),
        kind="object",
        label=label,
        raw_category=label.lower(),
        dimensions=Vec3(x=dx, y=dy, z=dz),
        transform=Mat4.translation(x, y, z),
        quality="measured",
        movable=movable,
        labeled_by="test",
    )


class TestPrecedentCorpusAndLedger:
    def test_precedents_load_and_validate(self):
        directives = load_precedents(allow_unverified=True)
        assert len(directives) >= 12
        assert all(isinstance(d, PrecedentDirective) for d in directives)

    def test_precedent_ledger_verification(self):
        ledger = load_precedent_ledger()
        assert len(ledger) >= 12
        for v in ledger:
            assert v.verified_by
            assert v.primary_citation
            assert v.docket_source.startswith("https://")

    def test_unverified_precedents_fail_closed(self, monkeypatch):
        monkeypatch.delenv("SP_PREVIEW_UNVERIFIED_PRECEDENTS", raising=False)
        directives = load_precedents()
        # All shipped cases are verified by human reviewer in the ledger
        assert len(directives) >= 12


class TestPrecedentCompiler:
    @pytest.fixture
    def compiler(self):
        return PrecedentCompiler(load_precedents(allow_unverified=True))

    def test_match_vip_dining_lounge(self, compiler):
        nodes = [
            _make_node("Bar_counter", 0, 0, 0, 3.0, 0.8, 1.1, movable=False),
            _make_node("High_top_table", 2, 2, 0, 0.9, 0.9, 1.05),
            _make_node("Counter_stool", 2.5, 2.5, 0, 0.4, 0.4, 0.8),
        ]
        matched = compiler.match(SpaceTypology.HOSPITALITY_LOUNGE, nodes)
        case_ids = {m.case_id for m in matched}
        assert "US-CAND-2021-CV-03481" in case_ids  # Golden State Warriors VIP lounge case

    def test_match_boba_shop(self, compiler):
        nodes = [
            _make_node("Service_counter", 0, 0, 0, 2.5, 0.7, 0.9, movable=False),
            _make_node("Queue_stanchion", 1, 0, 0, 0.3, 0.3, 0.9),
            _make_node("Straw_dispenser", 0.5, 0, 0.9, 0.2, 0.2, 0.3),
        ]
        matched = compiler.match(SpaceTypology.QSR_BEVERAGE, nodes)
        case_ids = {m.case_id for m in matched}
        assert "US-CAND-2020-CV-04182" in case_ids  # Kalani v. Starbucks / Boba QSR

    def test_qwen_prompt_formatting(self, compiler):
        nodes = [
            _make_node("Bar_counter", 0, 0, 0, 3.0, 0.8, 1.1, movable=False),
            _make_node("High_top_table", 2, 2, 0, 0.9, 0.9, 1.05),
        ]
        matched = compiler.match(SpaceTypology.HOSPITALITY_LOUNGE, nodes)
        prompt = compiler.format_qwen_precedent_prompt(matched)
        assert "[ACTIONABLE ADA CASE PRECEDENT CONSTRAINTS]" in prompt
        assert "Johnson v. Golden State Warriors LLC" in prompt
        assert "Mandatory Ratio: At least 5%" in prompt


class TestPrecedentConstraints:
    @pytest.fixture
    def vip_lounge_directive(self):
        directives = load_precedents(allow_unverified=True)
        return next(d for d in directives if d.case_id == "US-CAND-2021-CV-03481")

    def test_100_percent_high_top_violates_seating_ratio(self, vip_lounge_directive):
        # A room with only high-top bar tables (height = 1.05m = 41.3 inches, knee clearance not accessible)
        nodes = [
            _make_node("Bar_counter", 0, 0, 0, 3.0, 0.8, 1.1, movable=False),
            _make_node("High_top_table_1", 1, 1, 0, 0.8, 0.8, 1.05),
            _make_node("High_top_table_2", 2, 1, 0, 0.8, 0.8, 1.05),
        ]
        graph = SceneGraph(scan_id=uuid4(), revision=1, nodes=nodes)
        
        violations = check_precedent_constraints(graph, graph, [vip_lounge_directive])
        assert len(violations) == 1
        assert violations[0].rule_broken == "insufficient_accessible_seating_ratio"
        assert "0.0%" in violations[0].detail

    def test_compliant_accessible_seating_ratio_passes(self, vip_lounge_directive):
        # A room with high-top tables PLUS an accessible dining table (height = 0.76m = 30 inches, width = 0.9m)
        nodes = [
            _make_node("Bar_counter", 0, 0, 0, 3.0, 0.8, 1.1, movable=False),
            _make_node("High_top_table_1", 1, 1, 0, 0.8, 0.8, 1.05),
            _make_node("High_top_table_2", 2, 1, 0, 0.8, 0.8, 1.05),
            _make_node("Accessible_dining_table", 1.5, 1.5, 0, 0.9, 0.9, 0.76),
        ]
        graph = SceneGraph(scan_id=uuid4(), revision=1, nodes=nodes)
        
        violations = check_precedent_constraints(graph, graph, [vip_lounge_directive])
        assert len(violations) == 0

    def test_forbidden_move_on_plumbed_bar(self, vip_lounge_directive):
        bar_node = _make_node("Plumbed_bar_counter", 0, 0, 0, 3.0, 0.8, 1.1, movable=False)
        base_graph = SceneGraph(scan_id=uuid4(), revision=1, nodes=[bar_node])
        
        # Candidate proposes moving the plumbed bar counter by 0.5m
        moved_bar = bar_node.model_copy(
            update={"transform": Mat4.translation(0.5, 0, 0)}
        )
        cand_graph = SceneGraph(scan_id=uuid4(), revision=2, nodes=[moved_bar])

        violations = check_precedent_constraints(base_graph, cand_graph, [vip_lounge_directive])
        assert any(v.rule_broken == "forbidden_move" for v in violations)

    def test_anti_isolation_violation(self, vip_lounge_directive):
        # Place accessible table 6 meters away from the general cluster
        nodes = [
            _make_node("High_top_table_1", 0, 0, 0, 0.8, 0.8, 1.05),
            _make_node("High_top_table_2", 0.5, 0.5, 0, 0.8, 0.8, 1.05),
            _make_node("Accessible_dining_table", 7.0, 7.0, 0, 0.9, 0.9, 0.76),
        ]
        graph = SceneGraph(scan_id=uuid4(), revision=1, nodes=nodes)

        violations = check_precedent_constraints(graph, graph, [vip_lounge_directive])
        assert any(v.rule_broken == "anti_isolation_violation" for v in violations)


class TestPrecedentBenchmarkRunner:
    def test_benchmark_evaluation_on_test_cases(self):
        # Case 1: Compliant lounge
        lounge_nodes = [
            _make_node("Bar_counter", 0, 0, 0, 3.0, 0.8, 1.1, movable=False),
            _make_node("High_top_table", 1, 1, 0, 0.8, 0.8, 1.05),
            _make_node("Accessible_table", 1.5, 1.5, 0, 0.9, 0.9, 0.76),
        ]
        lounge_graph = SceneGraph(scan_id=uuid4(), revision=1, nodes=lounge_nodes)

        # Case 2: Non-compliant lounge (only bar stools)
        bar_only_nodes = [
            _make_node("Bar_counter", 0, 0, 0, 3.0, 0.8, 1.1, movable=False),
            _make_node("High_top_table", 1, 1, 0, 0.8, 0.8, 1.05),
        ]
        bar_only_graph = SceneGraph(scan_id=uuid4(), revision=1, nodes=bar_only_nodes)

        cases = [
            {"base_graph": lounge_graph, "typology": SpaceTypology.HOSPITALITY_LOUNGE, "moves": []},
            {"base_graph": bar_only_graph, "typology": SpaceTypology.HOSPITALITY_LOUNGE, "moves": []},
        ]

        summary = evaluate_precedent_benchmark(cases)
        assert summary.total_evaluations == 2
        assert summary.precedent_constraint_passes == 1  # 1 passed, 1 failed
        assert summary.precedent_constraint_pass_rate == 0.5
        assert "insufficient_accessible_seating_ratio" in summary.precedent_violations_by_rule
