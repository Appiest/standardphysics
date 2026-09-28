"""ADA layout directives: the corpus, the verification gate, the checker and the fix gate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from standardphysics_agents import assess
from standardphysics_agents.evaluation.precedent_benchmark import evaluate_precedent_benchmark
from standardphysics_agents.fix.search import combine_rejections, propose_fix
from standardphysics_agents.models import ModelAnswer
from standardphysics_agents.precedents import (
    PrecedentCompiler,
    check_precedent_constraints,
    directives_for_space,
    load_precedent_ledger,
    load_precedents,
    precedent_rejection_for,
    record_directive_review,
    rejection_for_space,
    sign_case_reference,
)
from standardphysics_agents.precedents.verification import PRECEDENTS_FILE, PREVIEW_REVIEWER
from standardphysics_agents.redesign import propose_redesign, validate_redesign
from standardphysics_agents.workflows import WHEELCHAIR_PROFILE, Workflow
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

    def test_a_card_reader_that_can_be_picked_up_may_move_to_the_lowered_section(self):
        reader = _box("Card reader", 0, 0, 0.1, size=0.1)
        room = _graph(reader)
        assert not check_precedent_constraints(room, _moved(room, reader, 0.2), [self.counter])

    def test_moving_a_built_in_point_of_sale_is_refused(self):
        reader = _box("Card reader", 0, 0, 0.1, size=0.1, movable=False)
        room = _graph(reader)
        violations = check_precedent_constraints(room, _moved(room, reader, 0.2), [self.counter])
        assert [v.rule_broken for v in violations] == ["moved_fixed_role"]


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


class TestSpaceTypeWiring:
    def test_no_space_type_means_no_veto(self, graph):
        assert rejection_for_space(None, graph, ALL) is None
        assert directives_for_space(None, graph, ALL) == []

    def test_the_shipped_ledger_enables_nothing_by_default(self, graph, monkeypatch, tmp_path):
        monkeypatch.delenv("SP_PREVIEW_UNVERIFIED_PRECEDENTS", raising=False)
        empty = tmp_path / "ledger.json"
        empty.write_text("[]")
        monkeypatch.setenv("STANDARDPHYSICS_PRECEDENT_LEDGER", str(empty))
        assert rejection_for_space(SpaceTypology.QSR_BEVERAGE, graph) is None

    def test_a_boba_shop_gets_the_directive_veto(self, graph):
        reject = rejection_for_space(SpaceTypology.QSR_BEVERAGE, graph, ALL)
        counter = next(n for n in graph.nodes if n.id == node_id("counter"))
        assert reject(graph, _moved(graph, counter, 0.05)) == "precedent_violation:moved_fixed_role"

    def test_combined_rejections_report_the_first_refusal(self, graph):
        combined = combine_rejections(None, lambda *_: None, lambda *_: "second", lambda *_: "third")
        assert combined(graph, graph) == "second"
        assert combine_rejections(None, None) is None


def _counter_marked_movable(graph: SceneGraph, label: str | None = None) -> SceneGraph:
    def mislabelled(node):
        update = {"movable": True} if label is None else {"movable": True, "label": label, "raw_category": node.label}
        return node.model_copy(update=update)

    return graph.model_copy(update={"nodes": [
        mislabelled(node) if node.id == node_id("counter") else node for node in graph.nodes
    ]})


# A counter is a fixture whatever its movable flag says, so the hard constraints
# already hold it still. Relabelled "Front desk" with the scanner's category kept,
# it is a service counter to the directives but not a named fixture: the
# mislabelled piece only the directives catch.
FRONT_DESK = "Front desk"


def _astra_answer(*moves: tuple[str, float, float]) -> ModelAnswer:
    return ModelAnswer(
        {"moves": [{"node_id": str(node_id(name)), "dx": dx, "dy": dy, "rotation_degrees": 0} for name, dx, dy in moves]},
        "test", "test/astra",
    )


class TestAstraRedesign:
    CASES_APART = (("case_west", -0.07, 0.0), ("case_east", 0.07, 0.0))

    def _validate(self, graph, scenario, pipeline, pack, ledger, answer, directives):
        return validate_redesign(
            graph, answer, [Workflow(id="room", title="Room", scenario=scenario)], [WHEELCHAIR_PROFILE], pipeline,
            rules=pack, ledger=ledger, directives=directives,
        )

    def test_a_counter_marked_movable_still_stays_put(self, graph, scenario, pipeline, pack, ledger):
        room = _counter_marked_movable(graph)
        answer = _astra_answer(*self.CASES_APART, ("counter", 0.0, -0.05))
        result = self._validate(room, scenario, pipeline, pack, ledger, answer, ())
        counter = next(node for node in result.graph.nodes if node.id == node_id("counter"))
        assert counter.transform.m == next(node for node in room.nodes if node.id == node_id("counter")).transform.m

    def test_a_mislabelled_counter_slides_through_without_directives(self, graph, scenario, pipeline, pack, ledger):
        room = _counter_marked_movable(graph, FRONT_DESK)
        answer = _astra_answer(*self.CASES_APART, ("counter", 0.0, -0.05))
        assert self._validate(room, scenario, pipeline, pack, ledger, answer, ()).accepted

    def test_the_directives_refuse_it(self, graph, scenario, pipeline, pack, ledger):
        room = _counter_marked_movable(graph, FRONT_DESK)
        directives = tuple(directives_for_space(SpaceTypology.QSR_BEVERAGE, room, ALL))
        answer = _astra_answer(*self.CASES_APART, ("counter", 0.0, -0.05))
        result = self._validate(room, scenario, pipeline, pack, ledger, answer, directives)
        assert not result.accepted
        assert result.reasons == ("precedent_violation:moved_fixed_role",)

    def test_the_right_fix_still_passes_with_directives(self, graph, scenario, pipeline, pack, ledger):
        directives = tuple(directives_for_space(SpaceTypology.QSR_BEVERAGE, graph, ALL))
        result = self._validate(graph, scenario, pipeline, pack, ledger, _astra_answer(*self.CASES_APART), directives)
        assert result.accepted

    def test_astra_sees_the_constraints_only_when_some_apply(self, graph, scenario, pipeline, pack, ledger):
        seen = []

        class Recorder:
            model = "test/astra"

            def structured(self, instruction, payload, schema, name):
                seen.append(payload)
                return _astra_answer(*TestAstraRedesign.CASES_APART)

        directives = tuple(directives_for_space(SpaceTypology.QSR_BEVERAGE, graph, ALL))
        workflows = [Workflow(id="room", title="Room", scenario=scenario)]
        for given in ((), directives):
            propose_redesign(graph, workflows, [WHEELCHAIR_PROFILE], [], pipeline,
                             rules=pack, ledger=ledger, model=Recorder(), directives=given)
        assert "ada_layout_constraints" not in seen[0]
        assert "904.4.1" in seen[1]["ada_layout_constraints"]


    def test_a_refused_layout_is_rebuilt_with_the_directive_s_own_words(self, graph, scenario, pipeline, pack, ledger):
        room = _counter_marked_movable(graph, FRONT_DESK)
        seen = []
        answers = iter([_astra_answer(*self.CASES_APART, ("counter", 0.0, -0.05)), _astra_answer(*self.CASES_APART)])

        class Rebuilder:
            model = "test/astra"

            def structured(self, instruction, payload, schema, name):
                seen.append(payload)
                return next(answers)

        directives = tuple(directives_for_space(SpaceTypology.QSR_BEVERAGE, room, ALL))
        result = propose_redesign(room, [Workflow(id="room", title="Room", scenario=scenario)], [WHEELCHAIR_PROFILE],
                                  [], pipeline, rules=pack, ledger=ledger, model=Rebuilder(), directives=directives)
        assert result.accepted and len(seen) == 2
        assert "refused_attempts" not in seen[0]
        told = seen[1]["refused_attempts"][0]
        assert told["reasons"] == ["precedent_violation:moved_fixed_role"]
        feedback = told["ada_directive_feedback"][0]
        assert feedback["what_broke"] == "moved_fixed_role" and feedback["requirement"]
        assert result.attempts == tuple(seen[1]["refused_attempts"])


class TestSignOffs:
    def test_a_directive_review_lands_in_the_ledger(self, tmp_path):
        path = tmp_path / "ledger.json"
        record_directive_review("service_counter", "A Reviewer", path)
        assert load_precedent_ledger(path).is_verified("service_counter")

    def test_a_sign_off_needs_a_real_name(self, tmp_path):
        with pytest.raises(ValueError):
            record_directive_review("service_counter", "  ", tmp_path / "ledger.json")
        with pytest.raises(ValueError):
            record_directive_review("service_counter", PREVIEW_REVIEWER, tmp_path / "ledger.json")

    def test_signing_a_case_puts_it_in_the_prompt(self, tmp_path):
        corpus = tmp_path / "precedents.json"
        corpus.write_text(PRECEDENTS_FILE.read_text())
        sign_case_reference("service_counter", "81 F. Supp. 3d 876 (N.D. Cal. 2015)", "A Reviewer", corpus)
        counter = next(d for d in load_precedents(corpus, allow_unverified=True) if d.directive_id == "service_counter")
        assert counter.verified_cases[0].verified_by == "A Reviewer"
        assert "Kalani" in PrecedentCompiler([counter]).format_qwen_precedent_prompt([counter])

    def test_signing_an_unknown_case_fails(self, tmp_path):
        corpus = tmp_path / "precedents.json"
        corpus.write_text(PRECEDENTS_FILE.read_text())
        with pytest.raises(KeyError):
            sign_case_reference("service_counter", "999 F.3d 1 (9th Cir. 2099)", "A Reviewer", corpus)
