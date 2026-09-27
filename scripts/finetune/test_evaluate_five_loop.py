import json
from types import SimpleNamespace

import evaluate_five_loop as evaluator
import pytest
import standardphysics_agents.training.feedback as feedback
from standardphysics_agents.training.reward import Verdict


class FakeChecker:
    scenario = "route"
    pinned = frozenset()
    measure = None
    owner_layout = None

    def assess(self, graph):
        return graph

    def fixable_problems(self, result):
        return list(range(result))


def setup_loop(monkeypatch, baseline=2, *, low_usability=()):
    checker = FakeChecker()
    data = SimpleNamespace(
        variants={"v": {"window_id": "w", "scan_id": "physical-shop"}},
        heldout=[{"variant": "v"}], checker=lambda _: checker, graph=lambda _: baseline,
    )
    scored = []

    def score(completion, current, _checker):
        scored.append((completion, current))
        if completion == "bad":
            return Verdict(0.0, parsed=True, hard_constraints_pass=True, reason="new problem: blocked exit")
        remaining = 0 if completion == "clear" else current - 1
        return Verdict(0.5, parsed=True, hard_constraints_pass=True, gate_accepts=True,
                       fixable_left=remaining, usability=0.7)

    monkeypatch.setattr(evaluator, "score_completion", score)
    monkeypatch.setattr(evaluator, "prompt_messages", lambda graph, _: [
        {"role": "system", "content": "fix room"}, {"role": "user", "content": str(graph)},
    ])
    monkeypatch.setattr(evaluator, "parse_edits", lambda completion: completion)
    monkeypatch.setattr(evaluator, "apply_edits", lambda graph, edits: 0 if edits == "clear" else graph - 1)
    monkeypatch.setattr(evaluator, "usability", lambda _before, _after, _owner, _: 0.5
                        if scored[-1][0] in low_usability else 1.0)
    monkeypatch.setattr(feedback, "room_view", lambda graph, _scenario, _problems: {"remaining": graph})
    monkeypatch.setattr(evaluator, "infer_wishes", lambda _graph, _measure: [])
    monkeypatch.setattr(evaluator, "explain_change", lambda *_args: SimpleNamespace(
        as_dict=lambda: {"moves": []}, text=lambda: "Here is what changed."))
    monkeypatch.setattr(evaluator, "_owner_outcome", lambda *_args: {})
    return data, scored


def test_rejected_move_stays_out_of_room_and_accepted_moves_get_rechecked(monkeypatch):
    data, scored = setup_loop(monkeypatch)
    replies = iter(("bad", "partial", "clear"))
    messages = []

    def sample(history):
        messages.append(json.loads(json.dumps(history)))
        return next(replies)

    record = evaluator.evaluate_variant(data, data.heldout[0], sample, "fake-qwen")
    assert scored == [("bad", 2), ("partial", 2), ("clear", 1)]
    assert [len(history) for history in messages] == [2, 4, 6]
    assert messages[1][-1]["role"] == "user"
    assert json.loads(messages[1][-1]["content"])["room"] == {"remaining": 2}
    assert json.loads(messages[2][-1]["content"])["room"] == {"remaining": 1}
    assert record["success"] and not record["abstained"]
    assert record["final_fixable_left"] == 0 and record["attempts_used"] == 3
    assert [attempt["accepted"] for attempt in record["attempts"]] == [False, True, True]
    assert record["attempts"][0]["verdict"]["reason"] == "new problem: blocked exit"
    assert record["scan_id"] == "physical-shop"


def test_partial_usability_loss_can_be_repaired_in_next_accepted_move(monkeypatch):
    data, scored = setup_loop(monkeypatch, low_usability=("partial",))
    replies = iter(("partial", "clear"))
    record = evaluator.evaluate_variant(data, data.heldout[0], lambda _: next(replies), "fake-qwen")
    assert scored == [("partial", 2), ("clear", 1)]
    first = record["attempts"][0]
    assert first["verdict"]["gate_accepts"] and first["accepted"]
    assert first["step_usability"] == 0.5
    assert first["candidate_baseline_usability"] == 0.5
    assert first["feedback"]["checker_feedback"]["current_baseline_usability"] == 0.5
    assert record["success"] and record["full_usability_preserved"]


def test_checker_clear_is_primary_even_when_strict_usability_is_lower(monkeypatch):
    data, _ = setup_loop(monkeypatch, low_usability=("clear",))
    record = evaluator.evaluate_variant(data, data.heldout[0], lambda _: "clear", "fake-qwen")
    assert record["success"] and record["checker_full_clear_within_five"]
    assert record["attempts"][0]["accepted"]
    assert record["final_baseline_usability"] == 0.5
    assert not record["full_usability_preserved"] and not record["abstained"]


def test_partial_internal_progress_is_not_presented_as_final_fix(monkeypatch):
    data, _ = setup_loop(monkeypatch, baseline=6)
    record = evaluator.evaluate_variant(data, data.heldout[0], lambda _: "partial", "fake-qwen")
    assert all(attempt["accepted"] for attempt in record["attempts"])
    assert record["final_fixable_left"] == 1
    assert not record["success"] and record["abstained"]


def test_all_five_rejections_abstain_without_reporting_a_fix(monkeypatch):
    data, scored = setup_loop(monkeypatch)
    record = evaluator.evaluate_variant(data, data.heldout[0], lambda _: "bad", "fake-qwen")
    assert len(scored) == 5 and {graph for _, graph in scored} == {2}
    assert not record["success"] and record["abstained"]
    assert record["final_fixable_left"] == 2 and record["stop_reason"] == "attempt_limit"


def test_empty_baseline_is_ineligible_and_never_sampled(monkeypatch):
    data, _ = setup_loop(monkeypatch, baseline=0)
    record = evaluator.evaluate_variant(data, data.heldout[0], lambda _: 1 / 0, "fake-qwen")
    assert record["attempts_used"] == 0 and not record["eligible"] and not record["success"]
    assert record["stop_reason"] == "no_fixable_findings"


def test_jsonl_output_has_one_record_per_variant_and_a_fixed_model_id(monkeypatch, tmp_path):
    data, _ = setup_loop(monkeypatch)
    output = tmp_path / "eval" / "five.jsonl"
    summary = evaluator.evaluate(data, lambda _: "clear", "fake-qwen", output)
    row = json.loads(output.read_text().strip())
    assert summary == {"variants": 1, "eligible_variants": 1, "fully_cleared": 1,
                       "fully_cleared_with_full_usability": 1, "cleared_with_construction": 0, "abstained": 0,
                       "resumed_variants": 0, "records": str(output)}
    assert row["model"] == "fake-qwen" and row["variant"] == "v"
    assert row["attempts"][0]["feedback"]["checker_feedback"]["fixable_left"] == 0


def test_interrupted_run_resumes_without_resampling_completed_variant(monkeypatch, tmp_path):
    data, _ = setup_loop(monkeypatch)
    data.variants["v2"] = {"window_id": "w", "scan_id": "second-physical-shop"}
    data.heldout.append({"variant": "v2"})
    output = tmp_path / "five.jsonl"
    calls = 0

    def interrupted(_messages):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise InterruptedError("sampler stopped")
        return "clear"

    with pytest.raises(InterruptedError):
        evaluator.evaluate(data, interrupted, "fake-qwen", output)
    assert calls == 2
    assert [json.loads(line)["variant"] for line in output.read_text().splitlines()] == ["v"]

    resumed_calls = 0

    def resume(_messages):
        nonlocal resumed_calls
        resumed_calls += 1
        return "clear"

    summary = evaluator.evaluate(data, resume, "fake-qwen", output)
    assert resumed_calls == 1
    assert summary["fully_cleared"] == 2 and summary["resumed_variants"] == 1
    assert [json.loads(line)["variant"] for line in output.read_text().splitlines()] == ["v", "v2"]
    with pytest.raises(ValueError, match="mismatched model"):
        evaluator.evaluate(data, resume, "another-model", output)
    with output.open("a") as handle:
        handle.write(output.read_text().splitlines()[0] + "\n")
    with pytest.raises(ValueError, match="duplicate saved variant"):
        evaluator.evaluate(data, resume, "fake-qwen", output)


def test_train_rows_can_be_sampled_without_evaluating_heldout(monkeypatch, tmp_path):
    data, _ = setup_loop(monkeypatch)
    data.variants["train"] = {"window_id": "w", "scan_id": "training-shop"}
    data.rl = [{"variant": "train"}]

    summary = evaluator.evaluate(data, lambda _: "clear", "fake-qwen", tmp_path / "train.jsonl",
                                 rows=data.rl)

    assert summary["variants"] == 1
    assert json.loads((tmp_path / "train.jsonl").read_text())["variant"] == "train"


def test_every_user_turn_is_illustrated_and_the_model_answers_stay_plain(monkeypatch):
    data, _ = setup_loop(monkeypatch)
    replies = iter(("bad", "partial", "clear"))
    seen = []

    def sample(history):
        seen.append(history)
        return next(replies)

    evaluator.evaluate_variant(data, data.heldout[0], sample, "fake-qwen",
                               illustrate=lambda message: {**message, "drawn": True})
    last = seen[-1]
    assert [message.get("drawn", False) for message in last] == [False, True, False, True, False, True]


def test_raw_attempts_record_seconds_and_the_servers_token_counts(monkeypatch):
    data, _ = setup_loop(monkeypatch)
    record = evaluator.evaluate_variant(data, data.heldout[0], lambda _: evaluator.Reply("clear", 1200, 40),
                                        "fake-qwen")
    attempt = record["attempts"][0]
    assert attempt["prompt_tokens"] == 1200 and attempt["completion_tokens"] == 40
    assert attempt["seconds"] >= 0 and attempt["completion"] == "clear"


def test_menu_turns_are_stateless_and_score_the_resolved_edits(monkeypatch):
    data, scored = setup_loop(monkeypatch)
    menu = SimpleNamespace(problem_view=[{"label": "P1"}], options=[], wish_view=[])
    lasts = []

    def messages(current, _checker, _menu, last):
        lasts.append(last)
        return [{"role": "system", "content": "menu"}, {"role": "user", "content": str(current)}]

    resolved = {'{"choose":[2]}': "bad", '{"choose":[1]}': "partial", '{"choose":[1,3]}': "clear"}
    monkeypatch.setattr(evaluator, "build_menu", lambda *_args: menu)
    monkeypatch.setattr(evaluator, "menu_messages", messages)
    monkeypatch.setattr(evaluator, "resolve", lambda reply, *_: SimpleNamespace(
        completion=resolved[reply], why="", as_dict=lambda: {"picks": json.loads(reply)["choose"]}))
    replies = iter(resolved)
    prompts = []

    def sample(history):
        prompts.append(history)
        return evaluator.Reply(next(replies), 900, 12)

    record = evaluator.evaluate_variant(data, data.heldout[0], sample, "fake-qwen", setup=evaluator.LoopSetup(interface="menu"))
    assert [len(history) for history in prompts] == [2, 2, 2]
    assert scored == [("bad", 2), ("partial", 2), ("clear", 1)]
    assert lasts[0] is None and lasts[1]["picks"] == [2] and not lasts[1]["accepted"]
    assert lasts[2]["accepted"] and lasts[2]["fixable_left"] == 1
    assert record["success"] and record["attempts"][0]["reply"] == '{"choose":[2]}'
    assert all(attempt["prompt_tokens"] == 900 for attempt in record["attempts"])


def test_menu_answers_on_a_real_room_never_break_a_hard_constraint():
    from pathlib import Path

    from standardphysics_agents.rules import VerificationLedger, load_pack
    from standardphysics_agents.training import TrainingChecker
    from standardphysics_contracts import Scenario, SceneGraph
    from standardphysics_pipeline import PipelineMeasurements

    fixture = Path(__file__).resolve().parents[2] / "packages/agents/tests/fixtures/placement-room.json"
    saved = json.loads(fixture.read_text())
    pack, ledger = load_pack(), VerificationLedger()
    for rule in pack.rules:
        ledger = ledger.record(rule, verified_by="test suite, not a person")
    checker = TrainingChecker(Scenario.model_validate(saved["scenario"]), rules=pack, ledger=ledger,
                              measure=PipelineMeasurements())
    room = SceneGraph.model_validate(saved["graph"])
    data = SimpleNamespace(variants={"v": {"window_id": "w", "scan_id": "s"}}, heldout=[{"variant": "v"}],
                           checker=lambda _: checker, graph=lambda _: room)
    replies = iter(('{"choose":[1,2,3,4,5,6],"why":"all"}', "not json"))
    record = evaluator.evaluate_variant(data, data.heldout[0], lambda _: next(replies), "fake-qwen",
                                        max_attempts=2, setup=evaluator.LoopSetup(interface="menu"))
    first, second = record["attempts"]
    assert first["verdict"]["hard_constraints_pass"] and first["accepted"]
    assert first["resolution"]["applied"] and first["menu"]["options"]
    assert second["resolution"]["interface"] == "unparseable"
    assert second["verdict"]["reason"] == "no_supported_furniture_move"


def test_a_change_the_owner_turns_down_is_put_back_and_their_words_reach_the_next_prompt(monkeypatch):
    from standardphysics_agents.training.owner import Review

    data, scored = setup_loop(monkeypatch)
    answers = iter((Review(accepted=False, said="keep the chairs at the table"), Review(accepted=True),
                    Review(accepted=True)))
    monkeypatch.setattr(evaluator, "_owner_for", lambda *_args: SimpleNamespace(
        review=lambda *_review_args: next(answers)))
    monkeypatch.setattr(evaluator, "build_menu", lambda *_args: SimpleNamespace(
        problem_view=[], options=[], wish_view=[]))
    lasts = []
    monkeypatch.setattr(evaluator, "menu_messages", lambda current, _checker, _menu, last: (
        lasts.append(last), [{"role": "system", "content": "menu"}, {"role": "user", "content": str(current)}])[1])
    monkeypatch.setattr(evaluator, "resolve", lambda reply, *_: SimpleNamespace(
        completion=reply, why="", as_dict=lambda: {"picks": [1]}))
    replies = iter(("partial", "partial", "clear"))
    record = evaluator.evaluate_variant(data, data.heldout[0], lambda _: next(replies), "fake-qwen",
                                        setup=evaluator.LoopSetup(interface="menu", owner="simulated"))
    first = record["attempts"][0]
    assert first["verdict"]["gate_accepts"] and not first["accepted"]
    assert first["owner"] == {"accepted": False, "said": "keep the chairs at the table"}
    assert "the owner turned it down" in first["feedback"]["checker_feedback"]["reason"]
    assert scored[1] == ("partial", 2)
    assert lasts[1]["owner_said"] == "keep the chairs at the table"
    assert record["success"] and record["attempts_used"] == 3


def test_the_first_option_chooser_needs_no_model_and_the_setup_follows_the_flags():
    from types import SimpleNamespace as Args

    assert json.loads(evaluator._first_option([]))["choose"] == [1]
    setup = evaluator._setup(Args(interface="menu", owner="simulated", menu_order="shuffled", wish_labels="hidden"))
    assert setup.owner == "simulated" and setup.view.order == "shuffled" and not setup.view.wishes_shown


def test_a_real_room_with_its_owner_in_the_loop_never_keeps_a_change_that_breaks_their_layout():
    from pathlib import Path

    from standardphysics_agents.rules import VerificationLedger, load_pack
    from standardphysics_agents.training import TrainingChecker
    from standardphysics_contracts import Scenario, SceneGraph
    from standardphysics_pipeline import PipelineMeasurements

    fixture = Path(__file__).resolve().parents[2] / "packages/agents/tests/fixtures/placement-room.json"
    saved = json.loads(fixture.read_text())
    pack, ledger = load_pack(), VerificationLedger()
    for rule in pack.rules:
        ledger = ledger.record(rule, verified_by="test suite, not a person")
    room = SceneGraph.model_validate(saved["graph"])
    checker = TrainingChecker(Scenario.model_validate(saved["scenario"]), rules=pack, ledger=ledger,
                              measure=PipelineMeasurements(), owner_layout=room)
    data = SimpleNamespace(variants={"v": {"window_id": "w", "scan_id": "s"}}, heldout=[{"variant": "v"}],
                           checker=lambda _: checker, graph=lambda _: room)
    record = evaluator.evaluate_variant(data, data.heldout[0], evaluator._first_option, "first-option",
                                        max_attempts=2,
                                        setup=evaluator.LoopSetup(interface="menu", owner="simulated"))
    assert {"hidden_wishes_kept", "owner_rejections", "layout_quality"} <= set(record)
    kept = [attempt for attempt in record["attempts"] if attempt["accepted"]]
    assert all(attempt["owner"]["accepted"] and attempt["explanation"]["moves"] for attempt in kept)
    assert record["hidden_wishes_kept"] == 1.0
