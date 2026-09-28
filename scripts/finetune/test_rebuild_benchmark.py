"""Regression checks for the rebuild benchmark's success and spend accounting."""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest
from rebuild_benchmark import Meter, Rebuild, _has_recovery_witness, _model, _shuffle_task, _trial_seed, summarize


def test_shuffle_excludes_rooms_with_preexisting_measured_failures(monkeypatch):
    window = SimpleNamespace(window_id="room", graph=object())
    monkeypatch.setattr("rebuild_benchmark.room", lambda _: (window, object()))
    monkeypatch.setattr("rebuild_benchmark.ledger", lambda *_: object())
    monkeypatch.setattr("rebuild_benchmark.measured_failures", lambda *_: [SimpleNamespace(key="fixed counter")])

    assert _shuffle_task(({"window_id": "room"}, 42, 3)) == ("preexisting_measured_failures", [])


def test_seeded_shuffles_are_distinct_reproducible_trials(monkeypatch):
    base = object()
    window = SimpleNamespace(window_id="room", graph=base)
    checker = object()
    monkeypatch.setattr("rebuild_benchmark.room", lambda _: (window, checker))
    monkeypatch.setattr("rebuild_benchmark.ledger", lambda *_: SimpleNamespace(
        measured_unknown=[], accept_for_final_layout=False))
    monkeypatch.setattr("rebuild_benchmark.measured_failures", lambda graph, *_: [] if graph is base else [
        SimpleNamespace(key="route")])
    monkeypatch.setattr("rebuild_benchmark.measures", lambda *_: {})
    monkeypatch.setattr("rebuild_benchmark._has_recovery_witness", lambda *_: True)

    def fake_scramble(_graph, _checker, _tries, *, seed, how):
        node = SimpleNamespace(id=seed, transform=SimpleNamespace(m=[seed]), movable=True)
        graph = SimpleNamespace(nodes=[node], model_dump=lambda **_: {"seed": seed})
        return [SimpleNamespace(name="v000", graph=graph)]

    monkeypatch.setattr("rebuild_benchmark.scramble", fake_scramble)
    status, rows = _shuffle_task(({"window_id": "room"}, 42, 3))

    assert status == "qualified"
    assert len(rows) == 3
    assert [row["seed"] for row in rows] == [_trial_seed(42, "room", index) for index in range(3)]
    assert all(row["shuffle_attempt"] == "v000" for row in rows)
    assert len({row["shuffle_id"] for row in rows}) == 3
    assert _trial_seed(42, "room", 0) != _trial_seed(43, "room", 0)


def test_shuffle_skips_a_failure_that_cannot_be_recovered(monkeypatch):
    base = object()
    window = SimpleNamespace(window_id="room", graph=base)
    monkeypatch.setattr("rebuild_benchmark.room", lambda _: (window, object()))
    monkeypatch.setattr("rebuild_benchmark.ledger", lambda *_: SimpleNamespace(
        measured_unknown=[], accept_for_final_layout=False))
    monkeypatch.setattr("rebuild_benchmark.measured_failures", lambda graph, *_: [] if graph is base else [
        SimpleNamespace(key="route")])
    monkeypatch.setattr("rebuild_benchmark.measures", lambda *_: {})

    def fake_scramble(*_args, **_kwargs):
        for index in range(2):
            node = SimpleNamespace(id=index, transform=SimpleNamespace(m=[index]), movable=True)
            graph = SimpleNamespace(nodes=[node], model_dump=lambda **_: {"variant": index})
            yield SimpleNamespace(name=f"v{index:03d}", graph=graph)

    monkeypatch.setattr("rebuild_benchmark.scramble", fake_scramble)
    monkeypatch.setattr("rebuild_benchmark._has_recovery_witness", lambda _, graph, __: graph.nodes[0].id == 1)

    status, rows = _shuffle_task(({"window_id": "room"}, 42, 1))

    assert status == "qualified"
    assert len(rows) == 1
    assert rows[0]["shuffle_attempt"] == "v001"
    assert rows[0]["graph"] == {"variant": 1}


def test_recovery_witness_requires_an_accepted_layout_with_no_ledger_failures(monkeypatch):
    shuffled = SimpleNamespace(model_dump=lambda **_: {"shuffled": True})
    monkeypatch.setattr("rebuild_benchmark.edits_between", lambda *_: object())
    monkeypatch.setattr("rebuild_benchmark.edits_json", lambda _: "return")
    monkeypatch.setattr("rebuild_benchmark.step", lambda *_: {"layout": {}, "failing": ["route"]})
    assert not _has_recovery_witness({}, shuffled, object())

    monkeypatch.setattr("rebuild_benchmark.step", lambda *_: {"failing": []})
    assert not _has_recovery_witness({}, shuffled, object())

    monkeypatch.setattr("rebuild_benchmark.step", lambda *_: {"layout": {}, "failing": []})
    assert _has_recovery_witness({}, shuffled, object())


def test_rebuild_continues_until_every_measured_failure_is_clear():
    rebuild = Rebuild({"window_id": "room", "graph": {}}, [{"role": "user", "content": "room"}], {})
    judged = {"verdict": {"reward": 0.5, "reason": "", "gate_accepts": True},
              "layout": {"revision": 2}, "failing": ["owner's existing failure"],
              "next_messages": [{"role": "user", "content": "still failing"}]}
    rebuild.advance(1, "move", judged)

    assert rebuild.active
    assert rebuild.reached_at is None
    assert rebuild.current == {"revision": 2}
    assert rebuild.messages == judged["next_messages"]

    rebuild.advance(2, "fix", {**judged, "layout": {"revision": 3}, "failing": []})
    assert not rebuild.active
    assert rebuild.reached_at == 2


def test_report_keeps_measured_and_verified_compliance_separate():
    row = {"measured_clear_at": 2, "owner_failures": ["fixed counter"], "start_failing": ["fixed counter", "route"],
           "owner_verified_for_final_layout": False, "start": {"usefulness": {"score": 0.5},
           "look": {"q": 0.3}, "amenity_usability": 0.8},
           "final": {"failing_total": 0, "measured_clear": True, "measured_unknown": ["knee clearance"],
                     "verified_for_final_layout": False, "usefulness_lost": [],
                     "usefulness_owner": {"score": 0.9}, "usefulness": {"score": 0.9},
                     "look": {"q": 0.7}, "amenity_usability": 1.0},
           "loops": [{"accepted": True}], "stopped": None}
    report = summarize([row])

    assert report["measured_clear"] == 1
    assert report["median_loops_when_clear"] == 2
    assert report["clear_with_amenities_preserved"] == 1
    assert report["verified_for_final_layout"] == 0
    assert report["unknown_measurements_final"] == 1
    assert report["distance_when_owner_compliant"]["rooms"] == 0
    assert report["ledger_failures"] == {"owner": 1, "shuffled": 2, "final": 0}


def test_meter_reloads_spend_and_reserves_for_a_retry(tmp_path):
    path = tmp_path / "spend.json"
    meter = Meter(path, budget=0.00005, sample_cap=2)
    meter.reserve(prompt_tokens=2, answers=1)
    meter.charge_answers([SimpleNamespace(length=2)], [SimpleNamespace(attempts=2, sample_tokens=0)])

    saved = json.loads(path.read_text())
    assert saved["prefill_tokens"] == 4
    assert saved["sample_tokens"] == 2
    resumed = Meter(path, budget=0.00005, sample_cap=2)
    assert resumed.spend.dollars == meter.spend.dollars
    with pytest.raises(RuntimeError, match="next round could reach"):
        resumed.reserve(prompt_tokens=10, answers=2)


def test_model_name_requires_state_for_trained_sampler():
    assert _model("base") == ("base", None)
    assert _model("sft=account/run/sft-state") == ("sft", "account/run/sft-state")
    with pytest.raises(argparse.ArgumentTypeError):
        _model("sft")
    with pytest.raises(argparse.ArgumentTypeError):
        _model("../output=state")
