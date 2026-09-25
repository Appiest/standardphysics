"""ADA layout directives: the corpus, the verification gate, the checker and the fix gate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from standardphysics_agents import assess
from standardphysics_agents.evaluation.precedent_benchmark import evaluate_precedent_benchmark
from standardphysics_agents.fix.search import propose_fix
from standardphysics_agents.precedents import (
    PrecedentCompiler,
    check_precedent_constraints,
    load_precedent_ledger,
    load_precedents,
    precedent_rejection_for,
)
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_contracts.precedents import SpaceTypology
from standardphysics_fixtures.shop import node_id

ALL = load_precedents(allow_unverified=True)


def _directive(directive_id: str):
    return next(d for d in ALL if d.directive_id == directive_id)


def _box(label: str, x: float, y: float, height: float, size: float = 0.8, movable: bool = True) -> SceneNode:
    return SceneNode(
        id=uuid4(),
        kind="object",
        label=label,
        raw_category=label.lower(),
        dimensions=Vec3(x=size, y=size, z=height),
        transform=Mat4.translation(x, y, height / 2),
        quality="measured",
        movable=movable,
        labeled_by="test",
    )


def _graph(*nodes: SceneNode) -> SceneGraph:
    return SceneGraph(scan_id=uuid4(), revision=1, nodes=list(nodes))


def _moved(graph: SceneGraph, node: SceneNode, dx: float, dy: float = 0.0) -> SceneGraph:
    shifted = node.model_copy(update={"transform": Mat4.translation(
        node.transform.position.x + dx, node.transform.position.y + dy, node.transform.position.z,
    )})
    return graph.model_copy(update={"nodes": [shifted if n.id == node.id else n for n in graph.nodes]})


BAR_TABLE_HEIGHT = 1.05
DINING_TABLE_HEIGHT = 0.76


class TestCorpus:
    def test_every_directive_rests_on_ada_sections(self):
        for directive in ALL:
            assert directive.authority
            assert all(section.startswith("ADA_2010_") for section in directive.authority)
            assert all(q.citation.startswith("ADA_2010_") for q in directive.inspection_queries)

    def test_every_linked_rule_exists_in_the_rulepack(self, pack):
        known = {rule.id for rule in pack.rules}
        linked = {q.rule_id for d in ALL for q in d.inspection_queries if q.rule_id}
        assert linked <= known

    def test_query_ids_are_unique_across_the_corpus(self):
        ids = [q.query_id for d in ALL for q in d.inspection_queries]
        assert len(ids) == len(set(ids))

    def test_a_signed_case_says_when_it_was_signed(self):
        for directive in ALL:
            for case in directive.case_references:
                assert bool(case.verified_by) == bool(case.verified_at)
                assert case.source_url.startswith("https://")

    def test_knee_clearance_is_a_measurement_request_not_a_guess(self):
        dining = _directive("accessible_dining_surfaces")
        knee = [q for q in dining.inspection_queries if q.citation.startswith("ADA_2010_306.3")]
        assert knee
        assert all(q.rule_id is None for q in knee)


class TestVerificationGate:
    def test_nothing_loads_without_a_ledger_entry(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SP_PREVIEW_UNVERIFIED_PRECEDENTS", raising=False)
        path = tmp_path / "ledger.json"
        path.write_text("[]")
        assert load_precedents(ledger=load_precedent_ledger(path)) == []

    def test_a_ledger_entry_enables_only_its_directive(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SP_PREVIEW_UNVERIFIED_PRECEDENTS", raising=False)
        path = tmp_path / "ledger.json"
        path.write_text(json.dumps([{
            "directive_id": "service_counter",
            "verified_by": "test suite, not a person",
            "verified_at": "2026-09-24T00:00:00Z",
        }]))
        loaded = load_precedents(ledger=load_precedent_ledger(path))
        assert [d.directive_id for d in loaded] == ["service_counter"]


class TestCompiler:
    def test_the_boba_shop_matches_counter_route_and_dining(self, graph):
        matched = PrecedentCompiler(ALL).match(SpaceTypology.QSR_BEVERAGE, graph.nodes)
        assert {d.directive_id for d in matched} == {
            "service_counter", "circulation_clear_width", "accessible_dining_surfaces",
        }

    def test_the_prompt_cites_sections_and_leaves_out_unsigned_cases(self):
        counter = _directive("service_counter")
        assert counter.case_references and not counter.verified_cases
        prompt = PrecedentCompiler(ALL).format_qwen_precedent_prompt([counter])
        assert "904.4.1" in prompt
        assert "Kalani" not in prompt

    def test_the_prompt_includes_a_signed_case(self):
        counter = _directive("service_counter")
        signed_case = counter.case_references[0].model_copy(
            update={"verified_by": "a reviewer", "verified_at": datetime.now(UTC)}
        )
        signed = counter.model_copy(update={"case_references": [signed_case]})
        prompt = PrecedentCompiler([signed]).format_qwen_precedent_prompt([signed])
        assert "Kalani v. Starbucks Corp., 81 F. Supp. 3d 876" in prompt


class TestChecker:
    dining = _directive("accessible_dining_surfaces")
    counter = _directive("service_counter")

    def test_bar_height_tables_only_break_226_1(self):
        room = _graph(_box("Bar table", 0, 0, BAR_TABLE_HEIGHT), _box("Bar table", 1, 0, BAR_TABLE_HEIGHT))
        violations = check_precedent_constraints(room, room, [self.dining])
        assert [v.rule_broken for v in violations] == ["too_few_accessible_dining_surfaces"]
        assert violations[0].authority == "ADA_2010_226.1"

    def test_one_table_in_range_satisfies_226_1(self):
        room = _graph(
            _box("Bar table", 0, 0, BAR_TABLE_HEIGHT),
            _box("Bar table", 1, 0, BAR_TABLE_HEIGHT),
            _box("Dining table", 0.5, 1, DINING_TABLE_HEIGHT),
        )
        assert check_precedent_constraints(room, room, [self.dining]) == []

    def test_a_table_below_28_inches_does_not_count(self):
        room = _graph(_box("Table", 0, 0, 0.65))
        assert check_precedent_constraints(room, room, [self.dining])

    def test_an_accessible_table_set_apart_breaks_226_2(self):
        room = _graph(
            _box("Bar table", 0, 0, BAR_TABLE_HEIGHT),
            _box("Bar table", 0.5, 0.5, BAR_TABLE_HEIGHT),
            _box("Dining table", 7, 7, DINING_TABLE_HEIGHT),
        )
        violations = check_precedent_constraints(room, room, [self.dining])
        assert [v.rule_broken for v in violations] == ["accessible_dining_set_apart"]

    def test_moving_the_ordering_counter_is_refused_even_if_marked_movable(self):
        counter = _box("Ordering counter", 0, 0, 0.9, size=2.0, movable=True)
        room = _graph(counter)
        violations = check_precedent_constraints(room, _moved(room, counter, 0.5), [self.counter])
        assert [v.rule_broken for v in violations] == ["moved_fixed_role"]
        assert violations[0].target_node_id == str(counter.id)

    def test_moving_the_card_reader_is_refused(self):
        reader = _box("Card reader", 0, 0, 0.1, size=0.1)
        room = _graph(reader)
        assert check_precedent_constraints(room, _moved(room, reader, 0.2), [self.counter])


class TestRejectionGate:
    def test_a_violation_the_room_already_had_does_not_block_a_move(self):
        chair = _box("Chair", 3, 3, 0.9, size=0.45)
        room = _graph(_box("Bar table", 0, 0, BAR_TABLE_HEIGHT), chair)
        reject = precedent_rejection_for([_directive("accessible_dining_surfaces")])
        assert reject(room, _moved(room, chair, 0.3)) is None

    def test_swapping_one_violation_for_another_is_refused(self):
        counter = _box("Ordering counter", 0, 3, 0.9, size=2.0)
        far_table = _box("Dining table", 9, 9, DINING_TABLE_HEIGHT)
        room = _graph(
            counter,
            _box("Bar table", 0, 0, BAR_TABLE_HEIGHT),
            _box("Bar table", 0.5, 0.5, BAR_TABLE_HEIGHT),
            far_table,
        )
        directives = [_directive("accessible_dining_surfaces"), _directive("service_counter")]
        fixed_table = _moved(room, far_table, -8.5, -8.5)
        swapped = _moved(fixed_table, counter, 0.5)
        assert len(check_precedent_constraints(room, room, directives)) == 1
        assert len(check_precedent_constraints(room, swapped, directives)) == 1
        reject = precedent_rejection_for(directives)
        assert reject(room, fixed_table) is None
        assert reject(room, swapped) == "precedent_violation:moved_fixed_role"


@pytest.fixture(scope="module")
def boba_run(graph, scenario, pipeline, pack, ledger):
    """The fixture shop through the fix search, gated by its matched directives."""
    matched = PrecedentCompiler(ALL).match(SpaceTypology.QSR_BEVERAGE, graph.nodes)
    before = assess(graph, scenario, pipeline, rules=pack, ledger=ledger)
    fixed = propose_fix(
        graph, scenario, pipeline, [f for f in before.findings if f.fix is not None],
        rules=pack, ledger=ledger, baseline=before,
        candidate_rejection=precedent_rejection_for(matched),
    )
    after = assess(fixed.graph, scenario, pipeline, rules=pack, ledger=ledger)
    return matched, before, fixed, after


class TestBobaShop:
    def test_the_shop_starts_with_a_high_counter_and_a_pinch(self, boba_run):
        _, before, _, _ = boba_run
        assert {f.check_id for f in before.problems} == {"service_counter_height", "route_clear_width"}

    def test_the_shop_breaks_no_directive_before_the_fix(self, boba_run, graph):
        matched, _, _, _ = boba_run
        assert check_precedent_constraints(graph, graph, matched) == []

    def test_the_fix_moves_only_the_display_cases(self, boba_run):
        _, _, fixed, _ = boba_run
        assert fixed.found
        assert {move.node_id for move in fixed.proposal.moves} == {node_id("case_west"), node_id("case_east")}

    def test_the_fix_clears_the_pinch_and_leaves_the_counter(self, boba_run):
        _, _, _, after = boba_run
        assert {f.check_id for f in after.problems} == {"service_counter_height"}

    def test_the_fix_breaks_no_directive(self, boba_run, graph):
        matched, _, fixed, _ = boba_run
        assert check_precedent_constraints(graph, fixed.graph, matched) == []


def test_benchmark_counts_one_pass_and_one_failure():
    compliant = _graph(_box("Bar table", 0, 0, BAR_TABLE_HEIGHT), _box("Dining table", 1, 1, DINING_TABLE_HEIGHT))
    bar_only = _graph(_box("Bar table", 0, 0, BAR_TABLE_HEIGHT))
    summary = evaluate_precedent_benchmark([
        {"base_graph": compliant, "typology": SpaceTypology.HOSPITALITY_LOUNGE, "moves": []},
        {"base_graph": bar_only, "typology": SpaceTypology.HOSPITALITY_LOUNGE, "moves": []},
    ])
    assert summary.precedent_constraint_passes == 1
    assert summary.precedent_violations_by_directive == {"accessible_dining_surfaces": 1}
