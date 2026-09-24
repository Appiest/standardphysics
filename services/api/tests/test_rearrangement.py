"""Suggest a rearrangement: the job, its answers, and the deployment it starts and stops. No network."""

import json

import pytest
from standardphysics_contracts import to_meters
from standardphysics_fixtures import FIX_SHIFT_INCHES, node_id

from conftest import drain
from standardphysics_api import rearrangement
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
    assert len(result["findings_after"]) < len(result["findings_before"])
    assert result["message"].startswith("Moving 1 piece clears 1 of the 3 problems")
    reward = result["reward"]
    assert 0 < reward["reward"] <= 1 and 0 < reward["recovered"] <= 1 and reward["usability"] == 1.0
    assert reward["all_clear"] is False and reward["disruption_meters"] > 0
    assert [attempt["accepted"] for attempt in result["attempts"]] == [False, True, False, False]
    assert [attempt["reason"] for attempt in result["attempts"]] == [
        "its answer wasn't a list of moves we could read", "",
        "it changed things by less than we can measure", "it pushed a piece past the edge of the room",
    ]
    assert client.get(f"/api/scans/{scan_id}/scene").json()["revision"] == 0


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
