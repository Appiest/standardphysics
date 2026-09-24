"""Suggest a rearrangement: the job, its answers, and the deployment it starts and stops. No network."""

import json
import uuid
from types import SimpleNamespace

import pytest
from standardphysics_agents.training.reward import Verdict
from standardphysics_agents.training.rooms import SMALL_SCAN_NODES
from standardphysics_contracts import Finding, Mat4, NodeMove, Vec3, to_meters
from standardphysics_fixtures import FIX_SHIFT_INCHES, build_lawsuit_graph, build_lawsuit_scenario, node_id

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
from standardphysics_api.rearrangement import Rearranger, scale_down_when_idle
from standardphysics_api.rearrangement_search import (
    MAX_WINDOWS_PER_SUGGESTION,
    Answer,
    scan_plan,
    whole_checker,
    window_seeds,
)

CASE_EAST = str(node_id("case_east"))
PATH = "/api/scans/{}/rearrangement-suggestion"


def _answer(dx: float, node: str = CASE_EAST) -> str:
    return json.dumps({"moves": [{"node_id": node, "dx": dx, "dy": 0, "rotation_degrees": 0}]})


FIX = _answer(round(to_meters(FIX_SHIFT_INCHES), 3))
NOISE = _answer(0.01)
TOO_FAR = _answer(3.0)
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
    model = FakeFireworks(answer=lambda messages: answers, **fake)
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
    assert result["message"] == ("Moving 1 piece clears 1 of the 4 problems furniture can fix here.")
    assert (result["model_calls"], result["windows"]) == (1, 0)
    reward = result["reward"]
    assert 0 < reward["reward"] <= 1 and 0 < reward["recovered"] <= 1 and reward["usability"] == 1.0
    assert reward["all_clear"] is False and reward["disruption_meters"] > 0
    assert [attempt["accepted"] for attempt in result["attempts"]] == [False, True, False, False]
    assert [attempt["reason"] for attempt in result["attempts"]] == [
        "its answer wasn't a list of moves we could read", "",
        "it changed things by less than we can measure", "it pushed a piece past the edge of the room",
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
    assert fixable(_findings(before)) == fixable(_findings(result["findings_before"])) == now == 4
    cleared = now - suggested
    assert result["message"] == f"Moving 1 piece clears {cleared} of the {now} problems furniture can fix here."


def test_when_nothing_passes_the_most_common_reason_is_the_message(make_client):
    clock = Clock()
    client, scan_id = _shop(make_client, _rearranger([NOISE, TOO_FAR, TOO_FAR, GARBAGE], clock))
    result = _suggest(client, scan_id)["result"]
    assert result["accepted"] is False and result["moves"] == [] and result["reward"] is None
    assert result["message"] == (
        "None of the 4 layouts the model tried passed our checks, "
        "mostly because it pushed a piece past the edge of the room."
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
    shop = build_lawsuit_graph()
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
    return [FIX] if CASE_EAST in messages[-1]["content"] else [GARBAGE]


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

    assert result["windows"] >= 2 and result["model_calls"] == result["windows"]
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
    monkeypatch.setattr(rearrangement_search, "prompt_messages", lambda graph, checker: [])
    monkeypatch.setattr(rearrangement_search, "_best_answer", lambda part, completions, found: next(answers))

    def whole_scan(plan, checker, moves):
        together = len(moves) > 1
        verdict = Verdict(0.0 if together else 0.5, gate_accepts=not together, reason="collided" if together else "")
        return Answer(verdict, moves)

    monkeypatch.setattr(rearrangement_search, "on_whole_scan", whole_scan)
    plan = SimpleNamespace(small=False)
    found = rearrangement_search.search(plan, None, [], lambda messages: [FIX])
    assert found.model_calls == 2 and found.whole_scan_reason == "collided"
    assert [str(move.node_id) for move in found.chosen.moves] == [second]
