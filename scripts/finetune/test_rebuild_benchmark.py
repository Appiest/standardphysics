"""Regression checks for the rebuild benchmark's success and spend accounting."""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest
from rebuild_benchmark import Meter, Rebuild, _model, _shuffle_task, summarize


def test_shuffle_excludes_rooms_with_preexisting_measured_failures(monkeypatch):
    window = SimpleNamespace(window_id="room", graph=object())
    monkeypatch.setattr("rebuild_benchmark.room", lambda _: (window, object()))
    monkeypatch.setattr("rebuild_benchmark.ledger", lambda *_: object())
    monkeypatch.setattr("rebuild_benchmark.measured_failures", lambda *_: [SimpleNamespace(key="fixed counter")])

    assert _shuffle_task({"window_id": "room"}) == ("preexisting_measured_failures", [])


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
    meter.charge(prompt_tokens=4, sample_tokens=2)

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
