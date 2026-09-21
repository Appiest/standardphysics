"""The router, and what happens to output that does not validate.

The whole argument for structured output is that something checks it. These are
the checks: every field read defensively, every contradiction named, and an
answer that fails any of them reaching no handler at all.
"""

from __future__ import annotations

import json

import pytest
from standardphysics_agents import assess
from standardphysics_agents.router import (
    ACTIONS,
    LocalPolicyRouter,
    Rejected,
    TypeSafeCallBudget,
    TypeSafeRouter,
    action_schema,
    extract_payload,
    parse_decision,
    state_for,
)
from standardphysics_contracts import Decision


@pytest.fixture(scope="module")
def findings(graph, scenario, pipeline, ledger, pack):
    return assess(graph, scenario, pipeline, rules=pack, ledger=ledger).findings


@pytest.fixture
def problem(findings):
    return next(f for f in findings if f.outcome == "problem")


@pytest.fixture
def passing(findings):
    return next(f for f in findings if f.outcome == "passes")


@pytest.fixture
def question(findings):
    return next(f for f in findings if f.outcome == "question")


def _parse(payload, findings):
    return parse_decision(payload, findings)


def _reason(payload, findings) -> str:
    result = _parse(payload, findings)
    assert isinstance(result, Rejected), f"expected a rejection, got {result}"
    return result.reason


class TestTheClosedSet:
    def test_the_actions_are_the_five_the_plan_names(self):
        assert ACTIONS == {"FIX", "RESCAN_AREA", "ASK_OWNER", "ESCALATE", "DONE"}

    def test_the_schema_comes_from_the_contract(self):
        """A schema written by hand drifts from Decision the first time either
        changes, and then valid output stops parsing."""
        assert set(action_schema()["properties"]) == set(Decision.model_fields)

    def test_the_schema_names_every_action_and_nothing_else(self):
        assert set(json.dumps(action_schema()).split('"')) >= ACTIONS


class TestMalformed:
    def test_nothing_at_all(self, findings):
        assert _reason(None, findings) == "empty_response"

    def test_an_empty_string(self, findings):
        assert _reason("", findings) == "empty_response"

    def test_whitespace(self, findings):
        assert _reason("   \n", findings) == "empty_response"

    def test_prose_instead_of_json(self, findings):
        assert _reason("I think you should fix the aisle", findings) == "not_json"

    def test_json_cut_off_mid_object(self, findings):
        assert _reason('{"action": "DO', findings) == "not_json"

    def test_json_cut_off_mid_array(self, findings):
        assert _reason('{"action": "FIX", "target_finding_ids": ["a', findings) == "not_json"

    def test_a_list_where_an_object_belongs(self, findings):
        assert _reason('[{"action": "DONE"}]', findings) == "not_an_object"

    def test_a_bare_string(self, findings):
        assert _reason('"DONE"', findings) == "not_an_object"

    def test_an_integer(self, findings):
        assert _reason(7, findings) == "not_an_object"

    def test_no_action_field(self, findings):
        assert _reason({"rationale": "looks fine"}, findings) == "no_action"

    def test_an_action_that_is_not_text(self, findings):
        assert _reason({"action": 3}, findings) == "no_action"

    def test_an_action_outside_the_set(self, findings):
        assert _reason({"action": "DELETE_EVERYTHING"}, findings) == "unknown_action"

    def test_the_right_action_in_the_wrong_case(self, findings):
        """A closed set is closed. Guessing that "fix" meant FIX is how a typo
        starts moving furniture."""
        assert _reason({"action": "fix"}, findings) == "unknown_action"

    def test_targets_that_are_not_a_list(self, findings):
        payload = {"action": "FIX", "target_finding_ids": "everything"}
        assert _reason(payload, findings) == "targets_not_a_list"

    def test_a_target_that_is_not_an_id(self, findings):
        payload = {"action": "FIX", "target_finding_ids": ["the narrow aisle"]}
        assert _reason(payload, findings) == "target_not_an_id"

    def test_a_target_id_for_a_finding_that_does_not_exist(self, findings):
        payload = {
            "action": "FIX",
            "target_finding_ids": ["11111111-2222-3333-4444-555555555555"],
        }
        assert _reason(payload, findings) == "target_not_in_findings"

    def test_a_question_that_is_not_text(self, findings):
        payload = {"action": "ASK_OWNER", "question": {"text": "hello"}}
        assert _reason(payload, findings) == "question_not_text"

    def test_a_question_longer_than_anybody_would_read(self, findings):
        payload = {"action": "ASK_OWNER", "question": "a" * 5000}
        assert _reason(payload, findings) == "question_too_long"


class TestContradictions:
    def test_fix_naming_nothing(self, findings):
        assert _reason({"action": "FIX"}, findings) == "fix_without_target"

    def test_rescan_naming_nothing(self, findings):
        assert _reason({"action": "RESCAN_AREA"}, findings) == "rescan_area_without_target"

    def test_escalate_naming_nothing(self, findings):
        assert _reason({"action": "ESCALATE"}, findings) == "escalate_without_target"

    def test_asking_without_a_question(self, findings):
        assert _reason({"action": "ASK_OWNER"}, findings) == "ask_without_question"

    def test_asking_with_an_empty_question(self, findings):
        payload = {"action": "ASK_OWNER", "question": "   "}
        assert _reason(payload, findings) == "ask_without_question"

    def test_done_that_still_names_something(self, findings, problem):
        payload = {"action": "DONE", "target_finding_ids": [str(problem.id)]}
        assert _reason(payload, findings) == "done_with_targets"

    def test_fixing_something_that_passed(self, findings, passing):
        """A fix moves furniture. Pointing one at a check that passed would
        rearrange a shop to solve nothing."""
        payload = {"action": "FIX", "target_finding_ids": [str(passing.id)]}
        assert _reason(payload, findings) == "fix_targets_something_that_passed"

    def test_fixing_a_question(self, findings, question):
        payload = {"action": "FIX", "target_finding_ids": [str(question.id)]}
        assert _reason(payload, findings) == "fix_targets_something_that_passed"

    def test_escalating_a_question(self, findings, question):
        payload = {"action": "ESCALATE", "target_finding_ids": [str(question.id)]}
        assert _reason(payload, findings) == "escalate_targets_something_that_is_not_a_problem"

    def test_typesafe_cannot_fix_a_problem_without_movable_furniture(
        self, findings, problem
    ):
        payload = {"action": "FIX", "target_finding_ids": [str(problem.id)]}
        answer = parse_decision(
            payload,
            findings,
            fixable_finding_ids=(),
        )
        assert isinstance(answer, Rejected)
        assert answer.reason == "fix_targets_unfixable_finding"

    def test_typesafe_cannot_rescan_a_finding_that_does_not_need_another_look(
        self, findings, question
    ):
        payload = {
            "action": "RESCAN_AREA",
            "target_finding_ids": [str(question.id)],
        }
        answer = parse_decision(
            payload,
            findings,
            rescan_finding_ids=(),
        )
        assert isinstance(answer, Rejected)
        assert answer.reason == "rescan_targets_finding_not_requesting_rescan"


class TestValid:
    def test_a_plain_done(self, findings):
        decision = _parse({"action": "DONE"}, findings)
        assert isinstance(decision, Decision)
        assert decision.action == "DONE"

    def test_a_fix_naming_a_real_problem(self, findings, problem):
        payload = {"action": "FIX", "target_finding_ids": [str(problem.id)]}
        decision = _parse(payload, findings)
        assert decision.action == "FIX"
        assert decision.target_finding_ids == [problem.id]

    def test_a_question_with_a_question_in_it(self, findings):
        payload = {"action": "ASK_OWNER", "question": "Is the front door heavy?"}
        assert _parse(payload, findings).question == "Is the front door heavy?"

    def test_json_text_parses_the_same_as_a_dict(self, findings, problem):
        payload = {"action": "FIX", "target_finding_ids": [str(problem.id)]}
        assert _parse(json.dumps(payload), findings) == _parse(payload, findings)

    def test_extra_fields_are_ignored_rather_than_fatal(self, findings):
        """A real service adds request ids and usage counters. None of that
        changes which branch runs."""
        payload = {"action": "DONE", "request_id": "abc", "usage": {"tokens": 12}}
        assert _parse(payload, findings).action == "DONE"

    def test_the_provider_is_recorded_on_the_decision(self, findings):
        decision = parse_decision({"action": "DONE"}, findings, "typesafe")
        assert decision.provider == "typesafe"


class TestResponseEnvelopes:
    def test_a_bare_object(self):
        assert extract_payload({"action": "DONE"}) == {"action": "DONE"}

    def test_wrapped_in_data(self):
        assert extract_payload({"data": {"action": "DONE"}}) == {"action": "DONE"}

    def test_wrapped_in_output(self):
        assert extract_payload({"output": {"action": "DONE"}}) == {"action": "DONE"}

    def test_an_openai_shaped_envelope(self):
        body = {"choices": [{"message": {"content": {"action": "DONE"}}}]}
        assert extract_payload(body) == {"action": "DONE"}

    def test_a_typesafe_system_one_envelope(self):
        body = json.loads(_typesafe_response("DONE"))
        assert extract_payload(body) == {"action": "DONE"}

    def test_json_text(self):
        assert extract_payload('{"action": "DONE"}') == {"action": "DONE"}

    def test_rubbish_comes_back_unchanged_for_the_parser_to_reject(self):
        assert extract_payload("not json at all") == "not json at all"


class FakeTransport:
    """A transport that returns whatever the test hands it."""

    def __init__(self, body: bytes | Exception) -> None:
        self.body = body
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, body, headers):
        self.calls.append((url, json.loads(body)))
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def _typesafe_response(action: str) -> bytes:
    return json.dumps(
        {
            "model": "jev-latest",
            "answers": {
                "action": {
                    "type": "choice",
                    "choice": action,
                    "probabilities": {
                        name: 1.0 if name == action else 0.0
                        for name in sorted(ACTIONS)
                    },
                    "confidence": 1.0,
                }
            },
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }
    ).encode()


def _router(body) -> tuple[TypeSafeRouter, FakeTransport]:
    transport = FakeTransport(body)
    router = TypeSafeRouter(
        api_key="test-key", base_url="https://typesafe.example", transport=transport
    )
    return router, transport


@pytest.fixture
def router_state(findings, graph, pack):
    return state_for(findings, graph, pack)


class TestTypeSafeClient:
    def test_no_key_authorizes_nothing(self, router_state, monkeypatch):
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        monkeypatch.delenv("TYPESAFE_BASE_URL", raising=False)
        answer = TypeSafeRouter().decide(router_state)
        assert isinstance(answer, Rejected)
        assert answer.reason == "typesafe_not_configured"

    def test_a_bare_contract_payload_cannot_authorize(self, router_state):
        router, _ = _router(b'{"action": "DONE"}')
        assert router.decide(router_state) == Rejected(
            "typesafe_response_missing_answers"
        )

    def test_a_non_choice_answer_cannot_authorize(self, router_state):
        body = json.dumps(
            {
                "model": "jev-latest",
                "answers": {"action": {"type": "noul", "noul": 1.0}},
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }
        ).encode()
        router, _ = _router(body)
        assert router.decide(router_state) == Rejected("no_action")

    def test_the_request_matches_the_system_one_contract(self, router_state):
        router, transport = _router(_typesafe_response("DONE"))
        router.decide(router_state)
        _, body = transport.calls[0]
        assert set(body) == {"state", "model", "questions"}
        assert body["state"] == router_state.summary()
        assert body["model"] == "jev-latest"
        assert body["questions"]["action"]["type"] == "choice"
        assert set(body["questions"]["action"]["criteria"]) == {"FIX", "ASK_OWNER", "ESCALATE"}
        assert "schema" not in body
        assert "instruction" not in body
        assert "input" not in body

    def test_the_request_carries_no_geometry_and_no_key(self, router_state):
        router, transport = _router(_typesafe_response("DONE"))
        router.decide(router_state)
        _, body = transport.calls[0]
        sent = json.dumps(body)
        assert "test-key" not in sent
        assert "transform" not in sent

    def test_a_transport_answer_comes_back_as_a_decision(self, router_state):
        router, _ = _router(_typesafe_response("FIX"))
        answer = router.decide(router_state)
        assert answer.action == "FIX"
        assert answer.provider == "typesafe"

    def test_a_fix_targets_only_movable_problems(self, router_state):
        router, _ = _router(_typesafe_response("FIX"))
        answer = router.decide(router_state)
        assert isinstance(answer, Decision)
        assert set(answer.target_finding_ids) == set(router_state.fixable_finding_ids)
        counter = next(
            f for f in router_state.problems if f.check_id == "service_counter_height"
        )
        assert counter.id not in answer.target_finding_ids

    def test_a_fix_cannot_exceed_the_attempt_budget(self, router_state):
        from dataclasses import replace
        router, _ = _router(_typesafe_response("FIX"))
        assert router.decide(replace(router_state, fix_attempts=3)) == Rejected("fix_budget_exhausted")

    def test_a_rescan_without_an_eligible_finding_authorizes_nothing(self, router_state):
        router, _ = _router(_typesafe_response("RESCAN_AREA"))
        assert router.decide(router_state) == Rejected("rescan_area_without_target")

    def test_a_type_safe_escalation_targets_only_stuck_problems(self, router_state):
        router, _ = _router(_typesafe_response("ESCALATE"))
        answer = router.decide(router_state)
        assert isinstance(answer, Decision)
        expected = {
            finding.id
            for finding in router_state.problems
            if finding.id not in router_state.fixable_finding_ids
        }
        assert set(answer.target_finding_ids) == expected

    def test_a_done_choice_never_targets_a_passing_finding(self, router_state, passing):
        from dataclasses import replace
        router, _ = _router(_typesafe_response("DONE"))
        answer = router.decide(replace(router_state, findings=[passing], fixable_finding_ids=()))
        assert isinstance(answer, Decision)
        assert passing.id not in answer.target_finding_ids

    def test_done_cannot_skip_measured_furniture_work(self, router_state):
        router, _ = _router(_typesafe_response("DONE"))
        assert router.decide(router_state) == Rejected("action_not_available")

    def test_rescan_is_not_repeated_while_furniture_work_remains(self, router_state):
        from dataclasses import replace
        state = replace(router_state, actions_taken=("RESCAN_AREA", "ASK_OWNER", "ESCALATE"),
                        rescan_finding_ids=(router_state.findings[0].id,))
        router, _ = _router(_typesafe_response("RESCAN_AREA"))
        assert router.request_body(state)["questions"]["action"]["criteria"].keys() == {"FIX"}
        assert router.decide(state) == Rejected("action_not_available")

    def test_a_truncated_answer_authorizes_nothing(self, router_state):
        body = _typesafe_response("FIX")[:-1]
        router, _ = _router(body)
        assert isinstance(router.decide(router_state), Rejected)

    def test_a_service_that_is_down_authorizes_nothing(self, router_state):
        router, _ = _router(OSError("connection refused"))
        answer = router.decide(router_state)
        assert isinstance(answer, Rejected)
        assert answer.reason == "transport_error"


class TestTheAnswerInTheTrace:
    """`decide` returns a `Decision`, which has no room for a confidence.

    How close the call was is worth having when a loop did something
    surprising, so the answer is traced where it arrives.
    """

    def test_the_answer_comes_back_decoded(self, router_state):
        answered = json.dumps(
            {
                "model": "jev-1.13.0",
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": "DONE",
                        "confidence": 0.35,
                        "probabilities": {"DONE": 0.4, "FIX": 0.38},
                    }
                },
                "usage": {"input_tokens": 1828, "output_tokens": 51},
            }
        ).encode()
        router, _ = _router(answered)
        answer = router.ask(router.request_body(router_state))
        assert answer["answers"]["action"]["confidence"] == 0.35
        assert answer["answers"]["action"]["probabilities"]["FIX"] == 0.38
        assert answer["usage"]["input_tokens"] == 1828

    def test_it_is_named_as_its_own_call(self):
        assert TypeSafeRouter.ask.traced_name == "router.typesafe.systemone"

    def test_a_service_that_is_down_is_not_mistaken_for_an_answer(self, router_state):
        router, _ = _router(OSError("connection refused"))
        assert router.ask(router.request_body(router_state)) == Rejected("transport_error")

    def test_the_budget_is_still_what_stops_a_paid_call(self, router_state):
        router, transport = _router(b'{"answers": {}}')
        router.budget = TypeSafeCallBudget(limit=1, used=1)
        assert router.ask(router.request_body(router_state)) == Rejected(
            "typesafe_call_budget_exhausted"
        )
        assert transport.calls == []


class TestLocalPolicy:
    def test_every_decision_says_it_came_from_the_local_policy(self, router_state):
        """The plan allows the fallback and says to drop the claim, not to make
        the same choices under the router's name."""
        assert LocalPolicyRouter().decide(router_state).provider == "local_policy"

    def test_thin_coverage_is_looked_at_before_anything_is_measured(
        self, graph, scenario, pipeline, pack, ledger
    ):
        """Measuring geometry nobody is sure of produces confident nonsense."""
        from standardphysics_agents.evaluation import variants as v

        unsure = v.set_quality(graph, v.CASE_EAST, "needs_another_look")
        found = assess(unsure, scenario, pipeline, rules=pack, ledger=ledger).findings
        state = state_for(found, unsure, pack)
        assert state.rescan_finding_ids
        assert LocalPolicyRouter().decide(state).action == "RESCAN_AREA"

    def test_furniture_is_tried_before_the_owner_is_troubled(self, router_state):
        assert router_state.fixable_finding_ids
        assert LocalPolicyRouter().decide(router_state).action == "FIX"

    def test_a_tape_measure_request_does_not_block_a_fix(
        self, findings, graph, pack
    ):
        """A-9. RoomPlan sees the doorway, not the 90-degree opening. That is
        a number to take with a tape, and it must not stop a rearrangement of
        the aisle."""
        door = next(f for f in findings if f.check_id == "door_clear_width")
        asked = door.model_copy(update={"outcome": "question", "fix": None})
        rewritten = [asked if f.id == door.id else f for f in findings]
        state = state_for(rewritten, graph, pack)
        assert asked.id not in state.rescan_finding_ids
        assert LocalPolicyRouter().decide(state).action == "FIX"

    def test_it_stops_asking_once_it_has_asked(self, findings, graph, pack):
        clear = [f for f in findings if f.outcome != "problem"]
        state = state_for(clear, graph, pack, actions_taken=("ASK_OWNER",))
        assert LocalPolicyRouter().decide(state).action == "DONE"

    def test_a_fix_it_proposes_only_ever_targets_problems(self, router_state):
        decision = LocalPolicyRouter().decide(router_state)
        problems = {f.id for f in router_state.problems}
        assert set(decision.target_finding_ids) <= problems

    def test_it_runs_out_of_fix_budget(self, findings, graph, pack):
        from standardphysics_agents.router import MAX_FIX_ATTEMPTS

        state = state_for(findings, graph, pack, fix_attempts=MAX_FIX_ATTEMPTS)
        assert LocalPolicyRouter().decide(state).action != "FIX"
