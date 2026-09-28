import json
from types import SimpleNamespace

import multiroom_data as data
import multiroom_train_data
import pytest
import standardphysics_agents.training.feedback as feedback
from standardphysics_agents.training.reward import Verdict


class FakeChecker:
    scenario = "route"
    scope = "layout"

    def assess(self, graph):
        return graph

    def fixable_problems(self, result):
        return list(range(result))


def test_correction_prefix_matches_live_feedback_contract(monkeypatch):
    monkeypatch.setattr(data.SceneGraph, "model_validate", lambda _: 2)
    monkeypatch.setattr(data, "checker_for", lambda _: FakeChecker())
    monkeypatch.setattr(data, "prompt_messages", lambda graph, _: [
        {"role": "system", "content": "fix room"}, {"role": "user", "content": str(graph)},
    ])
    monkeypatch.setattr(data, "searched_layout", lambda graph, _checker, rounds: graph - 1)
    monkeypatch.setattr(data, "edits_between", lambda before, after: SimpleNamespace(moves=[after]))
    monkeypatch.setattr(data, "edits_json", lambda edits: str(edits.moves[0]))
    monkeypatch.setattr(data, "score_completion", lambda target, graph, checker: Verdict(
        0.8, parsed=True, hard_constraints_pass=True, gate_accepts=True, fixable_left=graph - 1,
    ))
    monkeypatch.setattr(data, "node_moves", lambda edits: edits.moves)
    monkeypatch.setattr(data, "apply_moves", lambda graph, moves: moves[0])
    monkeypatch.setattr(data, "usability", lambda *_: 1.0)
    monkeypatch.setattr(feedback, "room_view", lambda graph, _scenario, _problems, _scope="layout": {"remaining": graph})

    rows = data.correction_rows({"variant_id": "v", "graph": {}}, SimpleNamespace(window_id="w"))

    assert len(rows) == 2
    assert [len(row["messages"]) for row in rows] == [3, 5]
    assert rows[0]["all_fixable_cleared"] is False
    assert rows[1]["all_fixable_cleared"] is True
    assert rows[1]["checker_scope"] == "trusted_geometry"
    assert rows[1]["messages"][3] == feedback.measured_feedback_message(
        1, FakeChecker(), accepted=True, reason="", fixable_left=1,
        parsed=True, hard_constraints_pass=True, step_usability=1.0,
        candidate_baseline_usability=1.0, current_baseline_usability=1.0,
    )


def test_corrections_resume_discards_unlogged_partial_rows(monkeypatch, tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "rl.jsonl").write_text('{"variant":"v1"}\n{"variant":"v2"}\n')
    (tmp_path / "variants.jsonl").write_text(
        '{"variant_id":"v1","window_id":"w"}\n{"variant_id":"v2","window_id":"w"}\n'
    )
    (tmp_path / "windows.jsonl").write_text('{"window_id":"w"}\n')
    (dataset / "corrections.jsonl").write_text(
        '{"variant":"v1","messages":[]}\n{"variant":"v2","messages":[]}\n'
    )
    (dataset / "corrections_log.jsonl").write_text('{"variant":"v1","steps":1}\n')
    monkeypatch.setattr(data, "Window", SimpleNamespace(from_dict=lambda row: row))
    monkeypatch.setattr(data, "correction_rows", lambda variant, window: [
        {"variant": variant["variant_id"], "messages": [], "all_fixable_cleared": True},
    ])

    data.run_corrections(tmp_path, data.Progress(tmp_path / "corrections_progress.json"))
    rows = [json.loads(line) for line in (dataset / "corrections.jsonl").read_text().splitlines()]

    assert [row["variant"] for row in rows] == ["v1", "v2"]


def test_rejected_model_attempt_gets_checked_search_target(monkeypatch):
    monkeypatch.setattr(data.SceneGraph, "model_validate", lambda _: 2)
    monkeypatch.setattr(data, "checker_for", lambda _: FakeChecker())
    monkeypatch.setattr(data, "prompt_messages", lambda graph, _: [
        {"role": "system", "content": "fix room"}, {"role": "user", "content": str(graph)},
    ])
    monkeypatch.setattr(data, "score_completion", lambda target, graph, checker: (
        Verdict(0.0, reason="unparseable") if target == "bad" else
        Verdict(0.8, parsed=True, hard_constraints_pass=True, gate_accepts=True, fixable_left=graph - 1)
    ))
    monkeypatch.setattr(data, "searched_layout", lambda graph, _checker, rounds: graph - 1)
    monkeypatch.setattr(data, "edits_between", lambda before, after: SimpleNamespace(moves=[after]))
    monkeypatch.setattr(data, "edits_json", lambda edits: str(edits.moves[0]))
    monkeypatch.setattr(feedback, "room_view", lambda graph, _scenario, _problems, _scope="layout": {"remaining": graph})
    checker_feedback = feedback.measured_feedback_message(
        2, FakeChecker(), accepted=False, reason="unparseable", fixable_left=2,
        parsed=False, hard_constraints_pass=False, step_usability=None,
        candidate_baseline_usability=None, current_baseline_usability=1.0,
    )
    trace = {"variant": "v", "window_id": "w", "scan_id": "shop", "baseline_fixable_left": 2,
             "attempts": [{"index": 1, "completion": "bad", "accepted": False,
                           "feedback": json.loads(checker_feedback["content"])}]}

    rows = data.trace_correction_rows({"variant_id": "v", "scan_id": "shop", "graph": {}},
                                      SimpleNamespace(window_id="w"), trace)

    assert len(rows) == 1
    assert rows[0]["messages"][-2] == checker_feedback
    assert rows[0]["messages"][-1] == {"role": "assistant", "content": "1"}
    assert rows[0]["after_attempt"] == 1


def test_trace_stage_rejects_heldout_variant(monkeypatch, tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "rl.jsonl").write_text('{"variant":"train"}\n')
    (tmp_path / "variants.jsonl").write_text(
        '{"variant_id":"train","window_id":"w","scan_id":"shop"}\n'
    )
    (tmp_path / "windows.jsonl").write_text('{"window_id":"w"}\n')
    trace = tmp_path / "heldout_trace.jsonl"
    trace.write_text('{"variant":"heldout","attempts":[]}\n')
    monkeypatch.setattr(data, "Window", SimpleNamespace(from_dict=lambda row: row))

    with pytest.raises(ValueError, match="held-out"):
        data.run_trace_corrections(tmp_path, trace)
    assert not (dataset / "corrections_from_trace.jsonl").exists()


def test_training_loader_adds_only_explicit_train_corrections(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "sft.jsonl").write_text('{"variant":"train","messages":[]}\n')
    (dataset / "rl.jsonl").write_text('{"variant":"train"}\n')
    (dataset / "heldout.jsonl").write_text('{"variant":"heldout"}\n')
    (dataset / "corrections.jsonl").write_text('{"variant":"train","messages":[]}\n')

    assert len(multiroom_train_data.load(tmp_path).sft) == 1
    assert len(multiroom_train_data.load(tmp_path, include_corrections=True).sft) == 2

    (dataset / "corrections_from_trace.jsonl").write_text('{"variant":"heldout","messages":[]}\n')
    with pytest.raises(ValueError, match="held-out"):
        multiroom_train_data.load(tmp_path, include_corrections=True)
