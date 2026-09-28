"""Suggest a rearrangement: the job, its answers, and the deployment it starts and stops. No network."""

import hashlib
import json
import math
import uuid
from types import SimpleNamespace

import pytest
from standardphysics_agents.fix import apply_moves, violations
from standardphysics_agents.training.edits import node_moves, parse_edits
from standardphysics_agents.training.rooms import SMALL_SCAN_NODES
from standardphysics_agents.training.snapped_prompt import openrouter_prompt_messages, prompt_messages
from standardphysics_agents.training.snapped_reward import MOVED_PINNED, Verdict, judge
from standardphysics_contracts import Finding, Mat4, NodeMove, Vec3, lies_flat, to_meters
from standardphysics_fixtures import (
    FIX_SHIFT_INCHES,
    build_crowded_counter_graph,
    build_lawsuit_graph,
    build_lawsuit_scenario,
    node_id,
)
from standardphysics_pipeline.footprints import distance_outside, floor_polygon

from conftest import drain
from standardphysics_api import rearrangement, rearrangement_search
from standardphysics_api.fireworks import (
    SCALING_UP,
    FakeFireworks,
    FireworksModel,
    ModelFailed,
    ModelWarming,
    Sampling,
    deployment_name,
)
from standardphysics_api.openrouter_rearrange import FakeOpenRouter, OpenRouterRearrange
from standardphysics_api.rearrangement import Rearranger, model_from_settings, scale_down_when_idle
from standardphysics_api.rearrangement_search import (
    MAX_WINDOWS_PER_SUGGESTION,
    Answer,
    scan_plan,
    whole_checker,
    window_seeds,
)
from standardphysics_api.settings import Settings

CASE_EAST = str(node_id("case_east"))
PATH = "/api/scans/{}/rearrangement-suggestion"


def _answer(dx: float, node: str = CASE_EAST) -> str:
    return json.dumps({"moves": [{"node_id": node, "dx": dx, "dy": 0, "rotation_degrees": 0}]})


FIX = _answer(round(to_meters(FIX_SHIFT_INCHES), 3))
NOISE = _answer(0.01)
TOO_FAR = _answer(3.0)
NEW_PROBLEM = _answer(0.4, str(node_id("case_west")))
GARBAGE = "Sure! I would move the display case a little to the left."


class Clock:
    def __init__(self):
        self.now, self.slept = 1_000.0, []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _rearranger(answers: list[str], clock: Clock, **fake) -> Rearranger:
    index = 0

    def next_answer(messages):
        nonlocal index
        answer = answers[min(index, len(answers) - 1)]
        index += 1
        return [answer]

    model = FakeFireworks(answer=next_answer, **fake)
    return Rearranger(model=model, keep_warm_seconds=300, clock=clock, sleep=clock.sleep)


def _shop(make_client, rearranger=None):
    client = make_client(seed=True, rearranger=rearranger).__enter__()
    drain(client)
    return client, client.get("/api/scans").json()["scans"][0]["id"]


def _suggest(client, scan_id: str, revision: int = 0) -> dict:
    started = client.post(PATH.format(scan_id), json={"base_revision": revision})
    assert started.status_code == 202, started.text
    assert started.json()["state"] == "queued"
    drain(client)
    return client.get(PATH.format(scan_id), params={"revision": revision}).json()


def test_without_a_model_the_feature_says_it_is_off(make_client):
    client, scan_id = _shop(make_client)
    status = client.get(PATH.format(scan_id), params={"revision": 0}).json()
    assert status["available"] is False and status["state"] == "idle"
    assert "model" in status["unavailable_reason"]
    refused = client.post(PATH.format(scan_id), json={"base_revision": 0})
    assert refused.status_code == 503
    assert refused.json()["error"] == status["unavailable_reason"]


def test_the_best_accepted_answer_comes_back_as_moves_and_nothing_is_saved(make_client):
    clock = Clock()
    client, scan_id = _shop(make_client, _rearranger([GARBAGE, FIX, NOISE, TOO_FAR], clock))
    status = _suggest(client, scan_id)

    assert status["state"] == "done" and status["phase"] is None
    result = status["result"]
    assert result["accepted"] is True
    assert [move["node_id"] for move in result["moves"]] == [CASE_EAST]
    assert result["moves"][0]["delta_translation"]["x"] == pytest.approx(to_meters(FIX_SHIFT_INCHES), abs=1e-3)
    assert result["graph_hash"]
    assert _problems(result["findings_after"]) < _problems(result["findings_before"])
    assert result["message"] == ("Moving 1 piece clears 1 of the 3 problems furniture can fix here.")
    assert (result["model_calls"], result["windows"]) == (2, 0)
    reward = result["reward"]
    assert 0 < reward["reward"] <= 1 and 0 < reward["recovered"] <= 1 and reward["usability"] == 1.0
    assert reward["all_clear"] is False and reward["disruption_meters"] > 0
    assert [attempt["accepted"] for attempt in result["attempts"]] == [False, True]
    assert [attempt["reason"] for attempt in result["attempts"]] == [
        "its answer wasn't a list of moves we could read", "",
    ]
    assert client.get(f"/api/scans/{scan_id}/scene").json()["revision"] == 0


def _findings(rows: list[dict]) -> list[Finding]:
    return [Finding.model_validate(row) for row in rows]


def _problems(findings: list[dict]) -> int:
    return sum(1 for finding in findings if finding["outcome"] == "problem")


def test_the_sentence_and_the_panel_count_the_same_problems(make_client):
    client, scan_id = _shop(make_client, _rearranger([FIX], Clock()))
    before = client.get(f"/api/scans/{scan_id}/assessment").json()["findings"]
    result = _suggest(client, scan_id)["result"]
    panel = client.post(f"/api/scans/{scan_id}/layout-checks",
                        json={"base_revision": 0, "sequence": 1, "moves": result["moves"]}).json()
    now, suggested = _problems(before), _problems(panel["findings"])
    assert (_problems(result["findings_before"]), _problems(result["findings_after"])) == (now, suggested)
    fixable = rearrangement.furniture_can_fix
    assert fixable(_findings(before)) == fixable(_findings(result["findings_before"])) == now == 3
    cleared = now - suggested
    assert result["message"] == f"Moving 1 piece clears {cleared} of the {now} problems furniture can fix here."


def test_when_nothing_passes_the_most_common_reason_is_the_message(make_client):
    clock = Clock()
    client, scan_id = _shop(make_client, _rearranger([NOISE, NEW_PROBLEM, NEW_PROBLEM, GARBAGE], clock))
    result = _suggest(client, scan_id)["result"]
    assert result["accepted"] is False and result["moves"] == [] and result["reward"] is None
    assert result["message"] == (
        "None of the 4 layouts the model tried passed our checks, "
        "mostly because it caused a new problem."
    )


def test_malformed_output_is_turned_down_in_plain_words(make_client):
    clock = Clock()
    client, scan_id = _shop(make_client, _rearranger([GARBAGE, "{", '{"moves": "left"}', ""], clock))
    result = _suggest(client, scan_id)["result"]
    assert result["accepted"] is False
    assert {attempt["reason"] for attempt in result["attempts"]} == {"its answer wasn't a list of moves we could read"}


def test_a_cold_deployment_is_waited_for_then_answers(make_client):
    clock, seen = Clock(), []
    rearranger = _rearranger([FIX], clock, warmups=2)
    client, scan_id = _shop(make_client, rearranger)

    def answer_and_note_the_phase(messages):
        seen.append(client.get(PATH.format(scan_id), params={"revision": 0}).json()["phase"])
        return [FIX]

    rearranger.model.answer = answer_and_note_the_phase
    status = _suggest(client, scan_id)
    assert seen == ["starting_model"]
    assert status["state"] == "done" and status["result"]["accepted"] is True
    assert clock.slept == [rearrangement.FIRST_RETRY_SECONDS, rearrangement.FIRST_RETRY_SECONDS * 1.5]
    assert rearranger.model.calls == ["allow_one_replica", "complete", "complete", "complete"]


def test_a_deployment_that_never_starts_fails_after_ten_minutes(make_client):
    clock = Clock()
    client, scan_id = _shop(make_client, _rearranger([FIX], clock, warmups=1_000))
    status = _suggest(client, scan_id)
    assert status["state"] == "failed" and status["error"] == rearrangement.STILL_STARTING
    assert sum(clock.slept) <= rearrangement.WARMUP_LIMIT_SECONDS
    assert client.get(f"/api/scans/{scan_id}").json()["state"] == "ready"


def test_the_deployment_runs_for_the_job_and_scales_to_zero_after_the_window(make_client):
    clock = Clock()
    rearranger = _rearranger([FIX], clock)
    client, scan_id = _shop(make_client, rearranger)
    _suggest(client, scan_id)
    database = client.app.state.database
    assert rearranger.model.calls == ["allow_one_replica", "complete"]

    clock.now += 299
    assert scale_down_when_idle(database, rearranger) is False
    clock.now += 2
    assert scale_down_when_idle(database, rearranger) is True
    assert rearranger.model.calls[-1] == "scale_to_zero"
    assert scale_down_when_idle(database, rearranger) is False


def test_the_deployment_still_scales_down_after_a_failed_job(make_client):
    clock = Clock()
    rearranger = _rearranger([FIX], clock, failure=ModelFailed("We couldn't reach the model. Try again in a minute."))
    client, scan_id = _shop(make_client, rearranger)
    status = _suggest(client, scan_id)
    assert status["state"] == "failed" and status["error"] == "We couldn't reach the model. Try again in a minute."
    clock.now += 301
    assert scale_down_when_idle(client.app.state.database, rearranger) is True
    assert rearranger.model.calls == ["allow_one_replica", "complete", "scale_to_zero"]


def test_a_second_suggestion_inside_the_window_reuses_the_warm_deployment(make_client):
    clock = Clock()
    rearranger = _rearranger([FIX], clock)
    client, scan_id = _shop(make_client, rearranger)
    database = client.app.state.database
    _suggest(client, scan_id)
    clock.now += 120
    assert scale_down_when_idle(database, rearranger) is False
    assert _suggest(client, scan_id)["result"]["accepted"] is True
    assert rearranger.model.calls == ["allow_one_replica", "complete", "complete"]

    clock.now += 299
    assert scale_down_when_idle(database, rearranger) is False
    clock.now += 2
    assert scale_down_when_idle(database, rearranger) is True
    assert rearranger.model.calls.count("scale_to_zero") == 1


def test_a_restart_fails_the_running_job_and_scales_the_deployment_down(make_client):
    clock = Clock()
    rearranger = _rearranger([FIX], clock)
    client, scan_id = _shop(make_client, rearranger)
    database = client.app.state.database
    with database.transaction() as connection:
        connection.execute("INSERT INTO rearrangements (scan_id, revision, phase) VALUES (?, 0, 'asking_model')",
                           (scan_id,))
        connection.execute("INSERT INTO jobs (scan_id, kind, revision, state, created_at)"
                           " VALUES (?, 'rearrange', 0, 'running', 'now')", (scan_id,))
        connection.execute("INSERT INTO rearrange_deployments (name, may_run) VALUES ('rearrange', 1)")
    worker = client.app.state.worker
    worker.start()
    worker.stop()
    status = client.get(PATH.format(scan_id), params={"revision": 0}).json()
    assert status["state"] == "failed" and status["error"] == rearrangement.INTERRUPTED
    scale_down_when_idle(database, rearranger)
    assert rearranger.model.calls == ["scale_to_zero"]


def test_a_stale_revision_is_refused(make_client):
    client, scan_id = _shop(make_client, _rearranger([FIX], Clock()))
    response = client.post(PATH.format(scan_id), json={"base_revision": 7})
    assert response.status_code == 409


def test_someone_elses_scan_is_not_found(make_client, stranger):
    client, scan_id = _shop(make_client, _rearranger([FIX], Clock()))
    assert stranger.post(PATH.format(scan_id), json={"base_revision": 0}).status_code == 404
    assert stranger.get(PATH.format(scan_id), params={"revision": 0}).status_code == 404


class Recorder:
    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, method, url, body, headers, timeout):
        self.requests.append((method, url, body, headers, timeout))
        return self.replies.pop(0)


def _fireworks(transport) -> FireworksModel:
    return FireworksModel(api_key="fw-secret", model="accounts/team/models/rearranger", deployment="dep1",
                          transport=transport)


def test_the_request_matches_the_training_evaluation_and_reads_every_choice():
    transport = Recorder((200, {"choices": [{"message": {"content": FIX}}, {"message": {"content": NOISE}}]}))
    answers = _fireworks(transport).complete([{"role": "user", "content": "{}"}], Sampling())
    assert answers == [FIX, NOISE]
    method, url, body, headers, timeout = transport.requests[0]
    assert (method, url) == ("POST", "https://api.fireworks.ai/inference/v1/chat/completions")
    assert body["n"] == 4 and body["temperature"] == 0.7 and body["max_tokens"] == 512
    assert body["model"] == "accounts/team/models/rearranger"
    assert headers == {"Authorization": "Bearer fw-secret"} and timeout == 300.0


def test_scaling_up_is_warming_and_other_errors_fail_without_the_key():
    warming = Recorder((503, {"error": {"code": SCALING_UP, "message": "retry in a few minutes"}}))
    with pytest.raises(ModelWarming):
        _fireworks(warming).complete([], Sampling())
    broken = Recorder((500, {"error": {"message": "boom"}}))
    with pytest.raises(ModelFailed) as failed:
        _fireworks(broken).complete([], Sampling())
    assert "fw-secret" not in str(failed.value) and "fw-secret" not in repr(_fireworks(broken))


def test_the_deployment_is_bounded_between_zero_and_one_replica():
    transport = Recorder((200, {}), (200, {}))
    model = _fireworks(transport)
    model.allow_one_replica()
    model.scale_to_zero()
    url = "https://api.fireworks.ai/v1/accounts/team/deployments/dep1"
    assert [(r[0], r[1], r[2]) for r in transport.requests] == [
        ("PATCH", url, {"minReplicaCount": 0, "maxReplicaCount": 1}),
        ("PATCH", url, {"minReplicaCount": 0, "maxReplicaCount": 0}),
    ]
    assert deployment_name("accounts/other/deployments/x", "accounts/team/models/m") == "accounts/other/deployments/x"


# --- big scans: problem windows ------------------------------------------------


def _floor_of_shops(copies: int, step: float = 12.0):
    """The sample shop repeated along x, far past the 80 nodes of one room; copy 0 keeps the fixture's ids."""
    shop = build_crowded_counter_graph()
    nodes = list(shop.nodes)
    for copy in range(1, copies):
        ids = {node.id: uuid.uuid5(node.id, f"copy {copy}") for node in shop.nodes}
        for node in shop.nodes:
            m = list(node.transform.m)
            m[3] += copy * step
            nodes.append(node.model_copy(update={
                "id": ids[node.id], "parent_id": ids.get(node.parent_id), "transform": Mat4(m=m),
            }))
    return shop.model_copy(update={"nodes": nodes})


def _only_the_case_east(messages):
    return [FIX] if CASE_EAST in messages[1]["content"] else [GARBAGE]


def test_a_big_scan_is_asked_about_window_by_window_and_the_moves_land_in_the_whole_scan(make_client):
    clock = Clock()
    model = FakeFireworks(answer=_only_the_case_east)
    client, scan_id = _shop(make_client, Rearranger(model=model, clock=clock, sleep=clock.sleep))
    big = _floor_of_shops(4)
    assert len(big.nodes) > SMALL_SCAN_NODES
    with client.app.state.database.transaction() as connection:
        connection.execute("UPDATE revisions SET graph_json=? WHERE scan_id=? AND revision=0",
                           (big.model_dump_json(), scan_id))
    result = _suggest(client, scan_id)["result"]

    assert result["windows"] >= 2 and result["windows"] <= result["model_calls"] <= result["windows"] * 4
    assert model.calls.count("complete") == result["model_calls"]
    assert result["accepted"] is True
    assert [move["node_id"] for move in result["moves"]] == [CASE_EAST]
    assert _problems(result["findings_after"]) < _problems(result["findings_before"])


def test_windows_go_largest_shortfall_first_and_stop_at_the_cap():
    shops = _floor_of_shops(6)
    plan = scan_plan(uuid.uuid4(), shops, build_lawsuit_scenario())
    checker = whole_checker(plan)
    problems = checker.fixable_problems(checker.assess(plan.graph))
    seeds = window_seeds(plan, problems)
    assert len(seeds) == MAX_WINDOWS_PER_SUGGESTION < len(problems)
    shortfall = {(f.check_id, round(f.locus.point.x, 3)): abs(f.required_inches - f.measured_inches) for f in problems}
    ranked = [shortfall[(seed["check"], round(seed["at"][0], 3))] for seed in seeds]
    assert ranked == sorted(ranked, reverse=True)


def _answer_in(reward: float, node: str) -> Answer:
    move = NodeMove(node_id=node, delta_translation=Vec3(x=0.1, y=0, z=0), delta_rotation_z_degrees=0)
    return Answer(Verdict(reward, parsed=True, hard_constraints_pass=True, gate_accepts=True), [move])


def test_when_the_windows_fail_together_the_best_single_window_is_used(monkeypatch):
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    answers = iter([_answer_in(0.4, first), _answer_in(0.6, second)])
    monkeypatch.setattr(rearrangement_search, "parts_to_ask", lambda plan, checker, problems: [SimpleNamespace(graph=None, checker=None)] * 2)
    def accepted_part(part, ask, found, provider, progress, index):
        found.model_calls += 1
        return next(answers)

    monkeypatch.setattr(rearrangement_search, "_ask_part", accepted_part)

    def whole_scan(plan, checker, moves):
        together = len(moves) > 1
        verdict = Verdict(0.0 if together else 0.5, gate_accepts=not together, reason="collided" if together else "")
        return Answer(verdict, moves)

    monkeypatch.setattr(rearrangement_search, "on_whole_scan", whole_scan)
    plan = SimpleNamespace(small=False)
    found = rearrangement_search.search(plan, None, [], lambda messages: [FIX])
    assert found.model_calls == 2 and found.whole_scan_reason == "collided"
    assert [str(move.node_id) for move in found.chosen.moves] == [second]


def test_openrouter_round_three_uses_noise_and_new_problem_feedback(make_client):
    west = str(node_id("case_west"))
    answers = iter([NEW_PROBLEM, NOISE, FIX])
    conversations = []

    def answer(messages):
        conversations.append(messages)
        return [next(answers)]

    client, scan_id = _shop(make_client, Rearranger(FakeOpenRouter(answer), provider="openrouter"))
    result = _suggest(client, scan_id)["result"]
    assert result["accepted"] is True and result["rounds"] == 3
    assert [attempt["reason"] for attempt in result["attempts"]] == [
        "it caused a new problem", "it changed things by less than we can measure", "",
    ]
    assert "New problem:" in conversations[1][3]["content"]
    assert conversations[1][2] == {"role": "assistant", "content": NEW_PROBLEM}
    assert "measurement's noise" in conversations[2][5]["content"]
    assert west in NEW_PROBLEM


def test_a_colliding_request_is_snapped_and_returned_as_the_legal_moves(make_client):
    west = str(node_id("case_west"))
    client, scan_id = _shop(make_client, _rearranger([_answer(-0.2, west)], Clock()))
    result = _suggest(client, scan_id)["result"]
    assert result["accepted"] is True
    assert result["model_calls"] == result["rounds"] == result["snap_rescues"] == 1
    assert result["moves"][0]["delta_translation"]["x"] == pytest.approx(-0.15)
    room = build_lawsuit_graph()
    moves = [NodeMove.model_validate(move) for move in result["moves"]]
    assert not violations(room, apply_moves(room, moves))


def test_four_refused_rounds_stop_and_keep_the_original_room(make_client):
    client, scan_id = _shop(make_client, _rearranger([GARBAGE], Clock()))
    result = _suggest(client, scan_id)["result"]
    assert result["accepted"] is False and result["rounds"] == result["model_calls"] == 4
    assert len(result["attempts"]) == 4 and all(not row["accepted"] for row in result["attempts"])
    assert client.get(f"/api/scans/{scan_id}/scene").json()["revision"] == 0


def test_budget_limit_stops_before_a_billable_request(make_client):
    model = OpenRouterRearrange(api_key="test", model="anthropic/claude-opus-5.5",
                                reasoning_effort="low", token_cap=4000, cost_cap_dollars=0.001,
                                price=lambda name: (0.000001, 0.000001))
    client, scan_id = _shop(make_client, Rearranger(model, provider="openrouter"))
    result = _suggest(client, scan_id)["result"]
    assert result["accepted"] is False and result["budget_reached"] is True
    assert result["model_calls"] == result["cost_dollars"] == 0
    assert "cost limit" in result["message"]


@pytest.mark.parametrize("cap", ["nan", "inf", "-inf", "0", "-0.5", "fifty cents"])
def test_a_cost_cap_no_request_could_exceed_is_refused_at_startup(monkeypatch, cap):
    monkeypatch.setenv("SP_REARRANGE_COST_CAP_DOLLARS", cap)
    with pytest.raises(ValueError, match="SP_REARRANGE_COST_CAP_DOLLARS must be a positive number of dollars"):
        Settings.from_environment()


def test_a_cost_cap_in_dollars_is_read(monkeypatch):
    monkeypatch.setenv("SP_REARRANGE_COST_CAP_DOLLARS", "1.25")
    assert Settings.from_environment().rearrange_cost_cap_dollars == 1.25


@pytest.mark.parametrize("cap", [math.nan, math.inf, 0.0])
def test_the_openrouter_model_refuses_a_cap_the_budget_check_cannot_hold(cap):
    with pytest.raises(ValueError, match="cost cap"):
        OpenRouterRearrange(api_key="test", model="anthropic/claude-opus-5.5", reasoning_effort="low",
                            token_cap=4000, cost_cap_dollars=cap, price=lambda name: (0.01, 0.02))


def test_openrouter_estimates_usage_when_the_provider_omits_it():
    model = OpenRouterRearrange(api_key="test", model="anthropic/claude-opus-5.5",
                                reasoning_effort="low", token_cap=4000, cost_cap_dollars=0.50,
                                price=lambda name: (0.01, 0.02))
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, cost="unknown"),
                               choices=[SimpleNamespace(message=SimpleNamespace(content="é"))])
    model._record(response, [{"role": "user", "content": "é"}])
    assert (model.prompt_tokens, model.completion_tokens) == (2, 2)
    assert model.cost_dollars == pytest.approx(0.06)


def test_provider_setting_selects_both_paths_without_network():
    assert isinstance(model_from_settings(Settings(rearrange_fake_model=True)), FakeOpenRouter)
    assert isinstance(model_from_settings(Settings(rearrange_provider="fireworks", rearrange_fake_model=True)),
                      FakeFireworks)
    assert isinstance(model_from_settings(Settings(openrouter_api_key="test")), OpenRouterRearrange)
    assert isinstance(model_from_settings(Settings(rearrange_provider="fireworks", rearrange_model="accounts/a/models/m",
                                                fireworks_api_key="test")), FireworksModel)


def test_teacher_record_and_saved_outcome_are_local_and_append_only(make_client):
    client, scan_id = _shop(make_client, _rearranger([FIX], Clock()))
    result = _suggest(client, scan_id)["result"]
    database = client.app.state.database
    with database.connect() as connection:
        accepted = connection.execute("SELECT payload_json FROM rearrangement_teacher_events WHERE kind='accepted'").fetchone()
    payload = json.loads(accepted["payload_json"])
    assert payload["source_graph_hash"] and payload["chains"][0]["rounds"][0]["proposal"] == FIX
    assert payload["accepted_moves"] == result["moves"] and payload["provider"] == "fireworks"
    saved = client.post(f"/api/scans/{scan_id}/revisions", json={
        "base_revision": 0, "moves": result["moves"], "suggestion_id": result["suggestion_id"],
    })
    assert saved.status_code == 201, saved.text
    with database.connect() as connection:
        kinds = [row["kind"] for row in connection.execute("SELECT kind FROM rearrangement_teacher_events ORDER BY id")]
    assert kinds == ["accepted", "saved"]


def test_put_back_records_a_separate_outcome(make_client):
    client, scan_id = _shop(make_client, _rearranger([FIX], Clock()))
    result = _suggest(client, scan_id)["result"]
    url = f"{PATH.format(scan_id)}/{result['suggestion_id']}/put-back?revision=0"
    assert client.post(url, json={}).status_code == 200
    with client.app.state.database.connect() as connection:
        kinds = [row["kind"] for row in connection.execute("SELECT kind FROM rearrangement_teacher_events ORDER BY id")]
    assert kinds == ["accepted", "put_back"]


def test_fireworks_prompt_stays_byte_for_byte_as_trained():
    plan = scan_plan(uuid.UUID(int=1), build_lawsuit_graph(), build_lawsuit_scenario())
    messages = prompt_messages(plan.graph, whole_checker(plan))
    encoded = json.dumps(messages, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == "2498a11b29e4d05ba6e2a21aa7058377fc55d0d206c2573868ec9bbc93800e58"
    openrouter = openrouter_prompt_messages(plan.graph, whole_checker(plan))
    assert "floor_inside_walls" in messages[1]["content"]
    assert "floor_polygon" in openrouter[1]["content"] and "floor_inside_walls" not in openrouter[1]["content"]


def test_a_pinned_piece_is_refused_not_snapped():
    plan = scan_plan(uuid.uuid4(), build_lawsuit_graph(), build_lawsuit_scenario())
    checker = whole_checker(plan)
    west = node_id("case_west")
    checker.pinned = frozenset({west})
    judged = judge(_answer(-0.2, str(west)), plan.graph, checker)
    assert judged.verdict.reason == MOVED_PINNED and judged.layout is None


def test_a_request_past_the_travel_limit_is_scored_on_a_legal_layout():
    plan = scan_plan(uuid.uuid4(), build_lawsuit_graph(), build_lawsuit_scenario())
    checker = whole_checker(plan)
    west = str(node_id("case_west"))
    far = node_moves(parse_edits(_answer(-1.6, west)))
    assert {item.kind for item in violations(plan.graph, apply_moves(plan.graph, far))} & {"moved_too_far", "left_the_floor"}
    judged = judge(_answer(-1.6, west), plan.graph, checker)
    assert judged.verdict.snapped_meters > 1.0
    assert judged.layout is None or not violations(plan.graph, judged.layout)


def test_a_snapped_layout_stays_out_of_a_keep_clear_zone():
    plan = scan_plan(uuid.uuid4(), build_lawsuit_graph(), build_lawsuit_scenario())
    west = next(node for node in plan.graph.nodes if node.id == node_id("case_west"))
    ramp = west.model_copy(update={
        "id": uuid.uuid4(), "kind": "ramp", "label": "Ramp", "movable": False,
        "dimensions": Vec3(x=0.2, y=0.8, z=0.1), "transform": Mat4.translation(-2.9, 0, 0.05),
    })
    room = plan.graph.model_copy(update={"nodes": [*plan.graph.nodes, ramp]})
    moves = node_moves(parse_edits(_answer(-0.2, str(west.id))))
    assert "blocked_keep_clear" in {item.kind for item in violations(room, apply_moves(room, moves))}
    judged = judge(_answer(-0.2, str(west.id)), room, whole_checker(plan))
    assert judged.layout is None or not violations(room, judged.layout)


def test_openrouter_floor_polygon_matches_the_checker_on_a_rotated_floor():
    shop = build_lawsuit_graph()
    floor = next(node for node in shop.nodes if lies_flat(node))
    case = next(node for node in shop.nodes if node.id == node_id("case_east"))
    sine = math.sqrt(0.5)
    floor = floor.model_copy(update={"transform": Mat4(m=[
        sine, -sine, 0, 0, sine, sine, 0, 0, 0, 0, 1, floor.transform.position.z, 0, 0, 0, 1,
    ])})
    case = case.model_copy(update={"transform": Mat4.translation(0, 0, case.transform.position.z)})
    room = shop.model_copy(update={"nodes": [floor, case]})
    checker = rearrangement_search.TrainingChecker(build_lawsuit_scenario())
    view = json.loads(openrouter_prompt_messages(room, checker)[1]["content"])
    polygon = floor_polygon(floor)
    assert view["floor_polygon"] == [list(point) for point in polygon]
    moves = node_moves(parse_edits(_answer(0.1, str(case.id))))
    assert "left_the_floor" not in {item.kind for item in violations(room, apply_moves(room, moves))}
    assert distance_outside(polygon, (4, 4), 0.01) > 0
    assert 4 < max(x for x, _ in polygon) and 4 < max(y for _, y in polygon)
