"""The fine-tuned labeller speaks to Fireworks' Serverless Training pool the way training rendered its prompts."""

import base64
import io
import json
import urllib.error

import pytest
import standardphysics_pipeline.astra as astra
import standardphysics_pipeline.tuned_labeller as tuned
from PIL import Image

STATE = "amelia-team/run-abc/epoch-1"
GOLDEN = (
    "<|im_start|>system\nLabel things.<|im_end|>\n<|im_start|>user\n{\"objects\": []}"
    "<|vision_start|><|image_pad|><|vision_end|><|vision_start|><|image_pad|><|vision_end|><|im_end|>\n"
    "<|im_start|>assistant\n<think>\n\n</think>\n\n"
)
"""Rendered by the cookbook's qwen3_8_disable_thinking_interleaved renderer used in training, images as pads."""


def photo(width: int, height: int) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 80, 40)).save(buffer, format="JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def chat_body(*urls: str) -> dict:
    content = [{"type": "text", "text": "{\"objects\": []}"}] + [{"type": "image_url", "image_url": {"url": u}} for u in urls]
    return {"model": "x", "max_tokens": 8192, "messages": [{"role": "system", "content": "Label things."},
                                                             {"role": "user", "content": content}]}


class FakePool:
    """Records every request; can answer 408 once on a job and 404 once on completions."""

    def __init__(self, busy_once: bool = False, drop_session_once: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.busy_once, self.drop_session_once = busy_once, drop_session_once
        self.sessions = 0

    def __call__(self, request, timeout=None):
        path = request.full_url.removeprefix(tuned.POOL_URL)
        payload = json.loads(request.data)
        self.calls.append((path, payload))
        assert request.headers["Authorization"] == "Bearer key"
        return FakeResponse(self.answer(path, payload))

    def answer(self, path: str, payload: dict) -> dict:
        if path == "/api/v1/create_session":
            self.sessions += 1
            return {"session_id": f"ts-{self.sessions}"}
        if path == "/api/v1/retrieve_future" and self.busy_once:
            self.busy_once = False
            raise urllib.error.HTTPError(path, 408, "try again", {}, io.BytesIO(b"{}"))
        if path == "/inference/v1/completions" and self.drop_session_once:
            self.drop_session_once = False
            raise urllib.error.HTTPError(path, 404, "gone", {}, io.BytesIO(b"Training session not found"))
        return RESPONSES[path](payload)


RESPONSES = {
    "/api/v1/create_model": lambda p: {"request_id": "fut-model"},
    "/api/v1/load_weights": lambda p: {"request_id": "fut-load"},
    "/api/v1/save_weights_for_sampler": lambda p: {"request_id": "fut-save"},
    "/api/v1/retrieve_future": lambda p: {
        "fut-model": {"model_id": "run-new:train:0"},
        "fut-load": {"path": STATE},
        "fut-save": {"path": "amelia-team/run-new/labeller-1"},
    }[p["request_id"]],
    "/api/v1/session_heartbeat": lambda p: {"type": "session_heartbeat"},
    "/inference/v1/completions": lambda p: {"choices": [{"text": "{\"nodes\": []}"}], "usage": {"prompt_tokens": 9}},
}


class FakeResponse:
    def __init__(self, body: dict):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def pool(monkeypatch):
    monkeypatch.setenv(tuned.STATE_ENV, STATE)
    monkeypatch.setenv(tuned.KEY_ENV, "key")
    monkeypatch.setattr(tuned, "_session", None)
    monkeypatch.setattr(tuned.time, "sleep", lambda seconds: None)
    fake = FakePool()
    monkeypatch.setattr(tuned.urllib.request, "urlopen", fake)
    return fake


def test_the_prompt_matches_what_training_rendered():
    body = chat_body(photo(64, 48), photo(64, 48))
    assert tuned.render_prompt(body["messages"]) == GOLDEN


def test_a_request_opens_a_session_from_the_saved_state_and_sends_shrunk_photos(pool):
    payload = tuned.transport("ignored", chat_body(photo(1024, 768)), {})

    assert payload == {"choices": [{"message": {"content": "{\"nodes\": []}"}}], "usage": {"prompt_tokens": 9}}
    paths = [path for path, _ in pool.calls]
    assert paths[0] == "/api/v1/create_session" and paths[-1] == "/inference/v1/completions"
    load = next(p for path, p in pool.calls if path == "/api/v1/load_weights")
    assert load["path"] == STATE and load["optimizer"] is False
    completion = pool.calls[-1][1]
    assert completion["model"] == "accounts/amelia-team/trainingSessions/ts-1/checkpoints/amelia-team/run-new/labeller-1"
    assert completion["prompt"].endswith("<think>\n\n</think>\n\n") and completion["temperature"] == 0.0
    assert completion["max_tokens"] == tuned.MAX_ANSWER_TOKENS
    [sent] = completion["images"]
    with Image.open(io.BytesIO(base64.b64decode(sent.split(",", 1)[1]))) as image:
        assert max(image.size) == tuned.IMAGE_SIDE


def test_the_session_is_opened_once_for_many_requests(pool):
    for _ in range(3):
        tuned.transport("ignored", chat_body(photo(64, 48)), {})
    assert [path for path, _ in pool.calls].count("/api/v1/create_session") == 1


def test_a_job_that_is_not_finished_is_asked_again(pool):
    pool.busy_once = True
    tuned.transport("ignored", chat_body(photo(64, 48)), {})
    assert [path for path, _ in pool.calls].count("/api/v1/retrieve_future") == 4


def test_a_dropped_session_is_reopened_once(pool):
    pool.drop_session_once = True
    payload = tuned.transport("ignored", chat_body(photo(64, 48)), {})
    assert payload["choices"][0]["message"]["content"] == "{\"nodes\": []}"
    assert pool.sessions == 2


def test_labelling_goes_to_the_tuned_model_only_when_a_state_is_set(monkeypatch):
    monkeypatch.delenv(tuned.STATE_ENV, raising=False)
    monkeypatch.setenv("LABEL_MODEL", "some/other-model")
    assert astra._label_model() == "some/other-model"

    monkeypatch.setenv(tuned.STATE_ENV, STATE)
    monkeypatch.setenv(tuned.KEY_ENV, "key")
    assert astra._label_model() == f"tuned:{STATE}"


def test_the_labeller_hands_its_requests_to_the_tuned_transport(monkeypatch):
    monkeypatch.setenv(tuned.STATE_ENV, STATE)
    monkeypatch.setenv(tuned.KEY_ENV, "key")
    seen = []

    def attempt(graph, objects, model, transport, *rest):
        seen.append((model, transport))
        return None

    monkeypatch.setattr(astra, "_attempt_with_model", attempt)
    graph = type("G", (), {"contents": lambda self: [object()]})()
    monkeypatch.setattr(astra, "object_mesh_profiles", lambda graph, path: {})
    astra._remote_patches(graph, None)
    assert seen == [(f"tuned:{STATE}", tuned.transport)]
