"""A configured model fixes the layout turn by turn from the menu, streamed to the owner."""

import json
import re
import urllib.error

from conftest import drain
from standardphysics_api.model_chooser import ModelChooser


def _sample(make_client):
    client = make_client(seed=True).__enter__()
    drain(client)
    return client, client.get("/api/scans").json()["scans"][0]["id"]


def _events(client, scan_id):
    response = client.post(f"/api/scans/{scan_id}/model-loop/stream", json={"base_revision": 0})
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _configure(monkeypatch, ask):
    monkeypatch.setenv("SP_LOOP_MODEL_URL", "http://model.test/v1")
    monkeypatch.setenv("SP_LOOP_MODEL", "fine-tune")
    monkeypatch.setenv("SP_LOOP_MODEL_LABEL", "Your fine-tuned model")
    monkeypatch.setattr(ModelChooser, "ask", ask)


def _first_option(self, messages):
    options = json.loads(messages[-1]["content"])["options"]
    return json.dumps({"choose": [options[0]["option"]], "why": "It clears P1 with the least moving."})


def test_the_button_is_hidden_until_a_model_is_set_up(make_client, monkeypatch):
    monkeypatch.delenv("SP_LOOP_MODEL_URL", raising=False)
    client, scan_id = _sample(make_client)
    assert client.get("/api/model-loop").json() == {"available": False, "label": ""}
    assert _events(client, scan_id)[-1]["kind"] == "failed"


def test_the_model_takes_turns_until_it_stops_and_the_moves_add_up(make_client, monkeypatch):
    _configure(monkeypatch, _first_option)
    client, scan_id = _sample(make_client)
    assert client.get("/api/model-loop").json() == {"available": True, "label": "Your fine-tuned model"}
    events = _events(client, scan_id)
    kinds = [event["kind"] for event in events]
    assert kinds[0] == "started" and kinds[-1] == "finished" and "turn" in kinds
    turns = [event for event in events if event["kind"] == "turn"]
    assert all(turn["picked"] and turn["why"] for turn in turns)
    owner_text = " ".join([*(words for turn in turns for words in turn["picked"]), *(turn["why"] for turn in turns)])
    assert not re.search(r"\[[0-9a-f]{4}\]|\bP\d+\b", owner_text)
    finished = events[-1]
    assert finished["moves"] and finished["explanation"]["fixed"] and finished["message"]
    assert finished["fixable_left"] <= turns[0]["fixable_left"] <= events[0]["fixable_left"]
    assert events[0]["fixable_left"] > 0


def test_an_unreachable_model_ends_the_stream_with_a_way_to_recover(make_client, monkeypatch):
    def down(self, messages):
        raise urllib.error.URLError("connection refused")

    _configure(monkeypatch, down)
    client, scan_id = _sample(make_client)
    last = _events(client, scan_id)[-1]
    assert last["kind"] == "failed" and "Try again" in last["message"]
