import json
from types import SimpleNamespace

import evaluate_five_loop as evaluator
import pytest
import standardphysics_agents.training.feedback as feedback
from standardphysics_agents.training.reward import Verdict


class FakeChecker:
    scenario = "route"

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
    monkeypatch.setattr(evaluator, "node_moves", lambda edits: edits)
    monkeypatch.setattr(evaluator, "apply_moves", lambda graph, moves: 0 if moves == "clear" else graph - 1)
    monkeypatch.setattr(evaluator, "usability", lambda _before, _after, _owner, _: 0.5
                        if scored[-1][0] in low_usability else 1.0)
    monkeypatch.setattr(feedback, "room_view", lambda graph, _scenario, _problems: {"remaining": graph})
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
                       "fully_cleared_with_full_usability": 1, "abstained": 0,
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
