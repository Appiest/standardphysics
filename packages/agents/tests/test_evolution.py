"""The outer loop, tested without TypeSafe or Astra.

A stand-in router repeats the owner question until its playbook holds a lesson,
then answers like the local policy. That is enough to prove the loop learns a
lesson from real failures, scores it on real cases, keeps it only when the
scores rise, and refuses one that changes nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from standardphysics_agents.evaluation.dataset import by_id
from standardphysics_agents.evolution import (
    Experience,
    Lesson,
    Playbook,
    acceptable,
    evolve,
    lesson_helps,
    load_memory,
    load_playbook,
    local_lesson,
    recall,
    reflect,
    remember,
    save_playbook,
)
from standardphysics_agents.router import LocalPolicyRouter, Rejected, TypeSafeRouter, parse_decision
from standardphysics_agents.router.state import RouterState
from standardphysics_agents.router.typesafe import INSTRUCTION

CASES = ("blocked_solid", "clean_shop_already_asked", "clean_shop")


class LearnsFromLessons:
    """Asks the owner again and again until a lesson says not to."""

    provider = "typesafe"

    def __init__(self, playbook: Playbook, *, listens: bool = True) -> None:
        self.playbook, self.listens = playbook, listens
        self.local = LocalPolicyRouter()

    def decide(self, state):
        learned = self.listens and self.playbook.for_room(state.room_kind)
        if learned or not state.questions:
            return self.local.decide(state)
        question = state.questions[0]
        return parse_decision(
            {"action": "ASK_OWNER", "target_finding_ids": [str(question.id)], "question": question.title},
            state.findings,
            self.provider,
        )


class NoAstra:
    model = "none"

    def structured(self, *_args, **_kwargs):
        return Rejected("openrouter_not_configured")


def _failure(case_id="blocked_solid", chose="ASK_OWNER", expected="ESCALATE", taken=("ASK_OWNER",), kind="service"):
    return Experience(
        case_id=case_id, room_kind=kind, problems=("exit_path",), actions_taken=taken,
        chose=chose, expected=expected,
        scores={"router_action_match": 0.0, "trajectory_ok": 0.0}, playbook_version=0,
    )


@pytest.fixture
def evolved(pack, ledger, pipeline, tmp_path):
    return evolve(
        cases=[by_id(case_id) for case_id in CASES],
        generations=3,
        router_factory=lambda playbook: LearnsFromLessons(playbook),
        measure=pipeline, rules=pack, ledger=ledger,
        memory_path=tmp_path / "experience.jsonl",
        publish=False, reflect_with=NoAstra(),
    )


class TestTheOuterLoop:
    def test_the_baseline_router_repeats_itself(self, evolved):
        assert evolved.baseline["trajectory_ok"] == 0.0

    def test_it_learns_a_lesson_and_keeps_it(self, evolved):
        first = evolved.generations[0]
        assert first.lesson is not None
        assert first.kept
        assert evolved.playbook.version == 1

    def test_the_lesson_names_the_cases_that_taught_it(self, evolved):
        assert set(evolved.generations[0].lesson.learned_from) <= {"blocked_solid", "clean_shop_already_asked"}

    def test_the_trial_includes_a_case_it_already_passed(self, evolved):
        assert "clean_shop" in evolved.generations[0].trial_cases

    def test_it_stops_once_nothing_fails(self, evolved):
        assert len(evolved.generations) == 1

    def test_the_final_playbook_is_scored_on_every_case(self, evolved):
        assert evolved.final["router_action_match"] == 1.0
        assert evolved.final["trajectory_ok"] == 1.0

    def test_what_the_checks_found_does_not_move(self, evolved):
        for name in ("finding_precision", "finding_recall", "question_recall"):
            assert evolved.final.get(name) == evolved.baseline.get(name), name

    def test_every_scored_case_is_remembered(self, evolved, tmp_path):
        memory = load_memory(tmp_path / "experience.jsonl")
        assert {item.playbook_version for item in memory} == {0, 1}

    def test_a_lesson_that_changes_nothing_is_refused(self, pack, ledger, pipeline, tmp_path):
        stubborn = evolve(
            cases=[by_id(case_id) for case_id in CASES],
            generations=3,
            router_factory=lambda playbook: LearnsFromLessons(playbook, listens=False),
            measure=pipeline, rules=pack, ledger=ledger,
            memory_path=None, publish=False, reflect_with=NoAstra(),
        )
        assert stubborn.generations
        assert not any(generation.kept for generation in stubborn.generations)
        assert stubborn.playbook.version == 0
        assert stubborn.final is None
        assert "nothing improved" in stubborn.generations[0].reasons

    def test_it_never_tries_the_same_lesson_twice(self, pack, ledger, pipeline):
        stubborn = evolve(
            cases=[by_id(case_id) for case_id in CASES],
            generations=5,
            router_factory=lambda playbook: LearnsFromLessons(playbook, listens=False),
            measure=pipeline, rules=pack, ledger=ledger,
            memory_path=None, publish=False, reflect_with=NoAstra(),
        )
        texts = [g.lesson.text for g in stubborn.generations if g.lesson is not None]
        assert len(texts) == len(set(texts))


class TestTheGate:
    def test_a_rise_with_nothing_falling_is_kept(self):
        before = {"a": {"router_action_match": 0.0, "trajectory_ok": 0.0}}
        after = {"a": {"router_action_match": 1.0, "trajectory_ok": 1.0}}
        assert lesson_helps(before, after) == (True, ())

    def test_fixing_one_case_by_breaking_another_is_refused(self):
        before = {"a": {"router_action_match": 0.0}, "b": {"router_action_match": 1.0}}
        after = {"a": {"router_action_match": 1.0}, "b": {"router_action_match": 0.0}}
        kept, reasons = lesson_helps(before, after)
        assert not kept
        assert reasons == ("nothing improved",)

    def test_any_score_falling_is_refused_even_with_a_net_gain(self):
        before = {"a": {"router_action_match": 0.0, "trajectory_ok": 1.0}, "b": {"router_action_match": 0.0}}
        after = {"a": {"router_action_match": 1.0, "trajectory_ok": 0.0}, "b": {"router_action_match": 1.0}}
        kept, reasons = lesson_helps(before, after)
        assert not kept
        assert reasons == ("trajectory_ok fell by 1",)

    def test_a_rise_in_measurement_error_is_refused(self):
        before = {"a": {"router_action_match": 0.0, "measurement_error_in": 0.0}}
        after = {"a": {"router_action_match": 1.0, "measurement_error_in": 2.0}}
        assert lesson_helps(before, after) == (False, ("measurement_error_in rose",))


class TestReflection:
    def test_a_repeated_request_becomes_a_lesson_about_repeating(self):
        lesson = local_lesson([_failure()], Playbook())
        assert "ASK_OWNER is already in actions_taken" in lesson.text
        assert "choose ESCALATE" in lesson.text
        assert lesson.source == "local_reflection"

    def test_a_wrong_choice_becomes_a_lesson_about_preferring(self):
        lesson = local_lesson([_failure(chose="ASK_OWNER", expected="FIX", taken=())], Playbook())
        assert lesson.text.startswith("Prefer FIX over ASK_OWNER")

    def test_a_rejected_answer_teaches_nothing(self):
        assert local_lesson([_failure(chose="rejected:transport_error")], Playbook()) is None

    def test_astras_lesson_is_used_when_it_is_acceptable(self):
        model = SimpleNamespace(
            model="openai/gpt-6-astra",
            structured=lambda *_, **__: SimpleNamespace(
                payload={"lesson": "Choose ESCALATE once the owner has been asked.", "room_kinds": ["service", "castle"]},
                model="openai/gpt-6-astra",
            ),
        )
        lesson = reflect([_failure()], [], Playbook(), model=model)
        assert lesson.source == "astra:openai/gpt-6-astra"
        assert lesson.room_kinds == ("service",)

    def test_a_lesson_about_thresholds_is_refused_for_the_local_one(self):
        model = SimpleNamespace(
            model="m",
            structured=lambda *_, **__: SimpleNamespace(payload={"lesson": "Lower the threshold to 30 inches.", "room_kinds": []}, model="m"),
        )
        lesson = reflect([_failure()], [], Playbook(), model=model)
        assert lesson.source == "local_reflection"

    def test_a_lesson_already_known_is_not_new(self):
        known = Playbook().with_lesson(Lesson("Choose DONE when nothing is left."))
        assert not acceptable("choose done when nothing is left.", known)


class TestThePlaybook:
    def test_a_lesson_makes_a_new_version(self):
        assert Playbook().with_lesson(Lesson("x")).version == 1

    def test_only_the_newest_eight_are_kept(self):
        playbook = Playbook()
        for index in range(10):
            playbook = playbook.with_lesson(Lesson(f"lesson {index}"))
        assert [lesson.text for lesson in playbook.lessons][0] == "lesson 2"

    def test_a_lesson_for_homes_stays_out_of_shops(self):
        playbook = Playbook().with_lesson(Lesson("Homes only.", room_kinds=("home",)))
        assert playbook.for_room("home") == ["Homes only."]
        assert playbook.for_room("service") == []

    def test_it_survives_a_round_trip_to_disk(self, tmp_path):
        playbook = Playbook().with_lesson(Lesson("Keep me.", room_kinds=("home",), learned_from=("a",)))
        assert load_playbook(save_playbook(playbook, tmp_path / "p.json")) == playbook

    def test_a_missing_playbook_is_an_empty_one(self, tmp_path):
        assert load_playbook(tmp_path / "missing.json") == Playbook()


class TestTypeSafeReadsThePlaybook:
    def test_with_no_lessons_the_instruction_is_unchanged(self):
        state = RouterState(findings=[], pass_number=1, room_kind="service")
        assert TypeSafeRouter(api_key="k").instructions(state) == INSTRUCTION

    def test_lessons_for_the_room_are_added(self):
        playbook = Playbook().with_lesson(Lesson("Choose DONE after one escalation.", room_kinds=("home",)))
        router = TypeSafeRouter(api_key="k", playbook=playbook)
        home = RouterState(findings=[], pass_number=1, room_kind="home")
        shop = RouterState(findings=[], pass_number=1, room_kind="service")
        assert router.request_body(home)["questions"]["action"]["instructions"].endswith(
            "(1) Choose DONE after one escalation."
        )
        assert router.instructions(shop) == INSTRUCTION


class TestMemory:
    def test_it_survives_a_round_trip(self, tmp_path):
        remember([_failure()], tmp_path / "m.jsonl")
        assert load_memory(tmp_path / "m.jsonl") == [_failure()]

    def test_recall_puts_the_same_kind_of_room_first(self):
        memory = [_failure("a", kind="home"), _failure("b", kind="service")]
        assert [item.case_id for item in recall(memory, [_failure(kind="service")])] == ["b", "a"]


def test_the_command_line_knows_evolve():
    from standardphysics_agents.cli import build_parser

    args = build_parser().parse_args(["evolve", "--generations", "2", "--preview-unverified", "--fresh"])
    assert (args.generations, args.preview_unverified, args.fresh) == (2, True, True)


class TestAstraIsAskedForASentence:
    def test_reflection_caps_the_answer(self):
        seen: dict = {}

        def structured(*_args, **kwargs):
            seen.update(kwargs)
            return Rejected("openrouter_not_configured")

        reflect([_failure()], [], Playbook(), model=SimpleNamespace(model="m", structured=structured))
        assert seen == {"max_tokens": 400}

    def test_the_cap_is_sent_when_asked_for(self):
        from standardphysics_agents.models import OpenRouter

        request = OpenRouter(api_key="k")._request("i", {}, {}, "n", max_tokens=400)
        assert request["max_tokens"] == 400

    def test_no_cap_is_sent_unless_asked_for(self):
        from standardphysics_agents.models import OpenRouter

        assert "max_tokens" not in OpenRouter(api_key="k")._request("i", {}, {}, "n")
