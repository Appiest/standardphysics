"""A configured model fixes the layout turn by turn from the menu, streamed to the owner."""

import json
import re
import threading
import urllib.error
import uuid

import pytest
from model_provider import Reply, completion, serve_provider
from standardphysics_agents.training.construction import FixtureMove
from standardphysics_agents.training.edits import TrainingEdits

from conftest import drain
from standardphysics_api import model_loop
from standardphysics_api.model_chooser import MAX_REPLY_BYTES, ModelChooser
from standardphysics_api.model_loop import _all_moves


def _sample(make_client, **settings):
    client = make_client(seed=True, **settings).__enter__()
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


def _first_option(self, messages, seconds=None):
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
    assert events[0]["fixable_left"] > 0 and events[0]["turns_at_most"] == 5
    assert 0 < len(events[0]["working_on"]) <= events[0]["fixable_left"]
    assert all(len(turn["working_on"]) <= turn["fixable_left"] for turn in turns)


def test_an_unreachable_model_ends_the_stream_with_a_way_to_recover(make_client, monkeypatch):
    def down(self, messages, seconds=None):
        raise urllib.error.URLError("connection refused")

    _configure(monkeypatch, down)
    client, scan_id = _sample(make_client)
    last = _events(client, scan_id)[-1]
    assert last["kind"] == "failed" and "Try again" in last["message"]


def test_a_turn_whose_menu_runs_out_of_time_ends_the_loop_without_asking_the_model(make_client, monkeypatch):
    asked = []
    _configure(monkeypatch, lambda self, messages: asked.append(messages) or _first_option(self, messages))
    monkeypatch.setattr("standardphysics_api.model_loop.LOOP_MENU_SECONDS", 0.0)
    client, scan_id = _sample(make_client)
    events = _events(client, scan_id)
    assert not asked
    assert [event["kind"] for event in events] == ["started", "finished"]
    assert events[-1]["moves"] == [] and events[-1]["fixable_left"] == events[0]["fixable_left"]


def test_a_built_in_slide_becomes_a_move_of_that_piece_the_plan_can_show():
    counter = uuid.uuid4()
    [move] = _all_moves(TrainingEdits(fixture_moves=[FixtureMove(node_id=counter, dx_inches=12, dy_inches=-6)]))
    assert move.node_id == counter and move.delta_rotation_z_degrees == 0
    assert round(move.delta_translation.x, 4) == 0.3048 and round(move.delta_translation.y, 4) == -0.1524
@pytest.fixture
def provider(monkeypatch):
    for served in serve_provider():
        monkeypatch.setenv("SP_LOOP_MODEL_URL", served.url)
        monkeypatch.setenv("SP_LOOP_MODEL", "fine-tune")
        monkeypatch.setenv("SP_LOOP_MODEL_LABEL", "Your fine-tuned model")
        monkeypatch.setenv("SP_LOOP_MODEL_REPLY_SECONDS", "30")
        yield served


def _first_option_reply(messages):
    options = json.loads(messages[-1]["content"])["options"]
    return Reply(completion(json.dumps({"choose": [options[0]["option"]], "why": "It clears the aisle."})))


def _ends_in_failure(client, scan_id, words):
    response = client.post(f"/api/scans/{scan_id}/model-loop/stream", json={"base_revision": 0})
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert response.status_code == 200
    assert [event["kind"] for event in events] == ["started", "failed"]
    assert words in events[-1]["message"] and "Try again" in events[-1]["message"]


def test_a_malformed_reply_ends_the_stream_with_a_failed_line(make_client, provider):
    provider.reply = Reply(b'{"choices": [{"message": {"content": "cut off')
    client, scan_id = _sample(make_client)
    _ends_in_failure(client, scan_id, "Your fine-tuned model sent an answer that couldn't be read")


def test_an_oversized_reply_ends_the_stream_with_a_failed_line(make_client, provider):
    provider.reply = Reply(completion("x" * MAX_REPLY_BYTES * 2), declare_length=False)
    client, scan_id = _sample(make_client)
    _ends_in_failure(client, scan_id, "sent an answer over 64 KB")


def test_a_slow_model_ends_the_stream_at_the_call_timeout(make_client, provider, monkeypatch):
    monkeypatch.setenv("SP_LOOP_MODEL_REPLY_SECONDS", "0.5")
    monkeypatch.setattr(model_loop, "clock", lambda: 0.0)
    provider.reply = Reply(completion("late"), delay=2.0)
    client, scan_id = _sample(make_client)
    _ends_in_failure(client, scan_id, "Your fine-tuned model took longer than 0.5 seconds to answer")


def test_the_loop_stops_with_what_it_found_when_its_time_budget_runs_out(make_client, provider, monkeypatch):
    now = [0.0]

    def each_reply_takes_the_whole_budget(messages):
        now[0] += model_loop.MODEL_LOOP_TURNS * 30
        return _first_option_reply(messages)

    provider.answer = each_reply_takes_the_whole_budget
    monkeypatch.setattr(model_loop, "clock", lambda: now[0])
    client, scan_id = _sample(make_client)
    events = _events(client, scan_id)
    assert [event["kind"] for event in events] == ["started", "turn", "finished"]
    assert events[-1]["moves"]
    assert events[-1]["message"] == "Stopped because the loop had used its 150 seconds. Open what it found so far."


def _stream_in_background(client, scan_id):
    lines: list[str] = []
    thread = threading.Thread(target=lambda: lines.append(
        client.post(f"/api/scans/{scan_id}/model-loop/stream", json={"base_revision": 0}).text))
    thread.start()
    return thread, lines


def test_a_second_loop_for_the_same_owner_is_refused_until_the_first_ends(make_client, provider):
    provider.answer = _first_option_reply
    provider.release = threading.Event()
    client, scan_id = _sample(make_client, max_owner_model_runs=1)
    thread, lines = _stream_in_background(client, scan_id)
    assert provider.received.wait(120)
    refused = client.post(f"/api/scans/{scan_id}/model-loop/stream", json={"base_revision": 0})
    provider.release.set()
    thread.join(300)
    assert refused.status_code == 429 and refused.headers["Retry-After"] == "30"
    assert "already working on this account" in refused.json()["error"]
    assert json.loads(lines[0].splitlines()[-1])["kind"] == "finished"
    assert _events(client, scan_id)[-1]["kind"] == "finished"


def test_the_server_refuses_a_loop_with_a_503_when_every_slot_is_taken(make_client, provider):
    provider.answer = _first_option_reply
    provider.release = threading.Event()
    client, scan_id = _sample(make_client, max_owner_model_runs=2, max_concurrent_model_runs=1)
    thread, _ = _stream_in_background(client, scan_id)
    assert provider.received.wait(120)
    refused = client.post(f"/api/scans/{scan_id}/model-loop/stream", json={"base_revision": 0})
    provider.release.set()
    thread.join(300)
    assert refused.status_code == 503 and "busy with other layouts" in refused.json()["error"]


def test_a_loop_on_a_missing_scan_is_refused_before_the_stream_starts(make_client, provider):
    client, _ = _sample(make_client)
    missing = "00000000-0000-4000-8000-000000000000"
    assert client.post(f"/api/scans/{missing}/model-loop/stream", json={"base_revision": 0}).status_code == 404


def _counter_with_register():
    from standardphysics_contracts import Mat4, SceneNode, Vec3, to_meters
    from standardphysics_fixtures import build_graph, node_id

    register = SceneNode(id=node_id("register"), kind="object", label="Cash register", raw_category="electronics",
                         dimensions=Vec3(x=0.35, y=0.28, z=0.25),
                         transform=Mat4.translation(0.3, 3.42, to_meters(47.0) + 0.125), movable=False)
    graph = build_graph()
    return graph.model_copy(update={"nodes": [*graph.nodes, register]})


def _prefer_a_section(reply_for):
    def choose(messages):
        options = json.loads(messages[-1]["content"])["options"]
        section = next((option for option in options if "section" in option["do"]), options[0])
        reply_for.append(section["do"])
        return json.dumps({"choose": [section["option"]], "why": "Customers can order at the lowered part."})
    return choose


def test_the_loop_offers_a_contractor_fix_for_a_counter_too_high_and_clears_it():
    from standardphysics_agents import VerificationLedger
    from standardphysics_agents.rules import load_pack
    from standardphysics_agents.training.checker import TrainingChecker
    from standardphysics_agents.training.owner import stated_book
    from standardphysics_fixtures import build_scenario
    from standardphysics_pipeline import PipelineMeasurements

    graph = _counter_with_register()
    ledger = VerificationLedger()
    for rule in load_pack().rules:
        ledger = ledger.record(rule, verified_by="test suite, not a person")
    checker = TrainingChecker(build_scenario(), rules=load_pack(), ledger=ledger,
                              measure=PipelineMeasurements(), owner_layout=graph, scope="fittings",
                              promoted=frozenset())
    loop = model_loop.ModelLoop(graph, checker, stated_book(graph, []))
    assert "service_counter_height" in {problem.check_id for problem in loop.open_problems()}
    picked: list[str] = []
    choose = _prefer_a_section(picked)
    for turn in range(1, model_loop.MODEL_LOOP_TURNS + 1):
        messages = loop.next_messages()
        if messages is None:
            break
        event = loop.take(turn, choose(messages))
        if any("section" in words for words in event.picked):
            assert event.construction and all("section" in words for words in event.construction)
            break
    assert any("section" in move for move in picked)
    assert "service_counter_height" not in {problem.check_id for problem in loop.open_problems()}
