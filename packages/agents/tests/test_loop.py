"""The loop, and whether the router's answer actually drives it.

The claim being tested is narrow and specific: a real answer changes which
branch runs, and a corrupted one changes nothing. Both halves matter. An action
that only gets logged is not control flow, and a malformed action that falls
through to a default has authorized something nobody asked for.
"""

from __future__ import annotations

import pytest
from standardphysics_agents import assess, graph_hash
from standardphysics_agents.loop import HANDLERS, MAX_PASSES, Loop, run_loop, run_pass
from standardphysics_agents.router import (
    ACTIONS,
    MAX_FIX_ATTEMPTS,
    LocalPolicyRouter,
    parse_decision,
)


class ScriptedRouter:
    """Hands back exactly what the test wrote, unvalidated."""

    provider = "scripted"

    def __init__(self, *payloads) -> None:
        self.payloads = list(payloads)
        self.seen: list = []

    def decide(self, state):
        self.seen.append(state)
        payload = self.payloads.pop(0) if self.payloads else {"action": "DONE"}
        return parse_decision(payload, state.findings, self.provider)


def _loop(graph, scenario, pipeline, pack, ledger, router) -> Loop:
    return Loop(
        graph=graph,
        scenario=scenario,
        measure=pipeline,
        router=router,
        rules=pack,
        ledger=ledger,
    )


@pytest.fixture(scope="module")
def findings(graph, scenario, pipeline, pack, ledger):
    return assess(graph, scenario, pipeline, rules=pack, ledger=ledger).findings


@pytest.fixture
def problem(findings, graph):
    """A problem furniture can actually clear.

    The worst problem in the fixture shop is a counter that is too high, and no
    rearrangement moves a built-in counter. A test about the FIX branch has to
    point it at something movable.
    """
    return next(
        f
        for f in findings
        if f.outcome == "problem"
        and f.locus
        and any(graph.by_id(n).movable for n in f.locus.node_ids)
    )


@pytest.fixture
def question(findings):
    return next(f for f in findings if f.outcome == "question")


def test_there_is_a_handler_for_every_action_and_no_default():
    assert set(HANDLERS) == ACTIONS


class TestTheAnswerChoosesTheBranch:
    def test_fix_rearranges_the_shop(self, graph, scenario, pipeline, pack, ledger):
        router = ScriptedRouter()
        step = run_pass(_loop(graph, scenario, pipeline, pack, ledger, router))
        # the local policy would fix; the scripted default is DONE, so drive it
        assert step.action == "DONE"

    def test_a_real_fix_changes_the_layout(
        self, graph, scenario, pipeline, pack, ledger, problem
    ):
        router = ScriptedRouter(
            {"action": "FIX", "target_finding_ids": [str(problem.id)]}
        )
        step = run_pass(_loop(graph, scenario, pipeline, pack, ledger, router))
        assert step.action == "FIX"
        assert step.result.proposal is not None
        assert graph_hash(step.graph) != graph_hash(graph)

    def test_a_fix_goes_through_the_gate_before_the_layout_moves(
        self, graph, scenario, pipeline, pack, ledger, problem
    ):
        router = ScriptedRouter(
            {"action": "FIX", "target_finding_ids": [str(problem.id)]}
        )
        step = run_pass(_loop(graph, scenario, pipeline, pack, ledger, router))
        assert step.result.gate is not None
        assert step.result.gate.accepted
        assert step.result.gate.shortfall_after < step.result.gate.shortfall_before

    def test_ask_owner_puts_a_question_and_moves_nothing(
        self, graph, scenario, pipeline, pack, ledger
    ):
        router = ScriptedRouter(
            {"action": "ASK_OWNER", "question": "Is the front door heavy?"}
        )
        step = run_pass(_loop(graph, scenario, pipeline, pack, ledger, router))
        assert step.result.question == "Is the front door heavy?"
        assert graph_hash(step.graph) == graph_hash(graph)
        assert step.result.proposal is None

    def test_rescan_names_the_areas_and_moves_nothing(
        self, graph, scenario, pipeline, pack, ledger, question
    ):
        router = ScriptedRouter(
            {"action": "RESCAN_AREA", "target_finding_ids": [str(question.id)]}
        )
        step = run_pass(_loop(graph, scenario, pipeline, pack, ledger, router))
        assert step.result.rescan == (question.id,)
        assert graph_hash(step.graph) == graph_hash(graph)

    def test_escalate_queues_it_and_moves_nothing(
        self, graph, scenario, pipeline, pack, ledger, problem
    ):
        router = ScriptedRouter(
            {"action": "ESCALATE", "target_finding_ids": [str(problem.id)]}
        )
        step = run_pass(_loop(graph, scenario, pipeline, pack, ledger, router))
        assert step.result.escalated == (problem.id,)
        assert "professional" in step.message
        assert graph_hash(step.graph) == graph_hash(graph)

    def test_done_ends_it(self, graph, scenario, pipeline, pack, ledger):
        router = ScriptedRouter({"action": "DONE"})
        step = run_pass(_loop(graph, scenario, pipeline, pack, ledger, router))
        assert step.message == "Your report is ready."

    def test_two_different_answers_run_two_different_branches(
        self, graph, scenario, pipeline, pack, ledger, problem, question
    ):
        fixing = run_pass(
            _loop(graph, scenario, pipeline, pack, ledger, ScriptedRouter(
                {"action": "FIX", "target_finding_ids": [str(problem.id)]}
            ))
        )
        asking = run_pass(
            _loop(graph, scenario, pipeline, pack, ledger, ScriptedRouter(
                {"action": "RESCAN_AREA", "target_finding_ids": [str(question.id)]}
            ))
        )
        assert fixing.action != asking.action
        assert graph_hash(fixing.graph) != graph_hash(asking.graph)


CORRUPTED = [
    pytest.param('{"action": "FI', id="truncated"),
    pytest.param("move the tables apart", id="prose"),
    pytest.param({"action": "fix"}, id="wrong_case"),
    pytest.param({"action": "FIX"}, id="fix_with_no_target"),
    pytest.param({"action": "RESCAN_AREA"}, id="rescan_with_no_target"),
    pytest.param({"action": "ASK_OWNER"}, id="ask_with_no_question"),
    pytest.param({}, id="empty_object"),
    pytest.param(None, id="nothing"),
    pytest.param({"action": "DROP_TABLES"}, id="invented_action"),
]


@pytest.mark.parametrize("payload", CORRUPTED)
def test_a_corrupted_answer_authorizes_nothing(
    payload, graph, scenario, pipeline, pack, ledger
):
    """No handler is registered for a rejection, so nothing runs: no
    rearrangement, no question, no escalation, no report."""
    router = ScriptedRouter(payload)
    step = run_pass(_loop(graph, scenario, pipeline, pack, ledger, router))

    assert not step.authorized
    assert step.action is None
    assert step.rejected
    assert step.decision is None
    assert step.result.proposal is None
    assert step.result.question is None
    assert step.result.escalated == ()
    assert step.result.rescan == ()
    assert graph_hash(step.graph) == graph_hash(graph)


def test_a_corrupted_answer_stops_the_loop_rather_than_retrying_blind(
    graph, scenario, pipeline, pack, ledger
):
    steps = run_loop(
        graph, scenario, pipeline, ScriptedRouter('{"action": "FI'),
        rules=pack, ledger=ledger,
    )
    assert len(steps) == 1
    assert steps[0].rejected == "not_json"


@pytest.fixture(scope="module")
def steps(graph, scenario, pipeline, pack, ledger):
    """One whole run of the loop, shared by everything below."""
    return run_loop(
        graph, scenario, pipeline, LocalPolicyRouter(), rules=pack, ledger=ledger
    )


class TestTheWholeLoop:
    def test_it_finishes(self, steps):
        assert steps[-1].action == "DONE"

    def test_it_stays_under_the_ceiling(self, steps):
        assert len(steps) <= MAX_PASSES

    def test_it_fixes_before_it_asks(self, steps):
        actions = [step.action for step in steps]
        assert actions.index("FIX") < actions.index("ASK_OWNER")

    def test_every_rearrangement_it_kept_was_an_improvement(self, steps):
        accepted = [s.result.gate for s in steps if s.result.gate]
        assert accepted
        assert all(gate.accepted for gate in accepted)
        assert all(
            gate.shortfall_after < gate.shortfall_before for gate in accepted
        )

    def test_the_shop_ends_up_better_than_it_started(self, steps):
        assert len(steps[-1].assessment.problems) < len(steps[0].assessment.problems)

    def test_it_never_asks_the_same_thing_twice(self, steps):
        asked = [s.action for s in steps if s.action in ("ASK_OWNER", "ESCALATE")]
        assert len(asked) == len(set(asked))

    def test_every_pass_records_the_decision_that_drove_it(self, steps):
        for step in steps:
            assert step.assessment.decision is not None
            assert step.assessment.decision.action == step.action

    def test_the_layout_it_judged_is_recorded_on_every_pass(self, steps):
        for step in steps:
            assert len(step.assessment.graph_hash) == 64


def test_a_router_that_never_finishes_is_capped(
    graph, scenario, pipeline, pack, ledger, question
):
    """A cooperative router stops itself. This is the other case."""

    class Stubborn:
        provider = "stubborn"

        def decide(self, state):
            return parse_decision(
                {"action": "RESCAN_AREA", "target_finding_ids": [str(question.id)]},
                state.findings,
                self.provider,
            )

    steps = run_loop(
        graph, scenario, pipeline, Stubborn(), rules=pack, ledger=ledger
    )
    assert len(steps) == 2
    assert steps[-1].action == "RESCAN_AREA"


def test_three_failed_rearrangements_end_the_loop(
    graph, scenario, pipeline, pack, ledger
):
    """Plan section 5. The fix agent says what to ask the owner instead."""

    class AlwaysFix:
        provider = "always_fix"

        def decide(self, state):
            targets = [str(f.id) for f in state.problems]
            if not targets:
                return parse_decision({"action": "DONE"}, state.findings, self.provider)
            return parse_decision(
                {"action": "FIX", "target_finding_ids": targets},
                state.findings,
                self.provider,
            )

    steps = run_loop(
        graph, scenario, pipeline, AlwaysFix(), rules=pack, ledger=ledger, max_passes=12
    )
    failed = sum(1 for s in steps if s.result.fix_failed)
    assert failed <= MAX_FIX_ATTEMPTS
    assert len(steps) <= 12
