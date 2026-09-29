"""Reading a whole walk quickly without losing a frame to the host's limits.

Pinned here: which hosts are asked to skip reasoning (and that OpenRouter never
is, because its default model rejects it with a 400 that is never retried),
that an answer given with reasoning is never served from the cache as one
given without, that a rate limit waits as long as the host asks and gets more
attempts than a server blip, and that a frame the first pass lost is asked for
again rather than silently dropped.
"""

from __future__ import annotations

import io
import json
import types
import urllib.error

import pytest
from PIL import Image
from standardphysics_pipeline.discovery import detect, detector_transport
from standardphysics_pipeline.discovery.cache import DetectionCache
from standardphysics_pipeline.discovery.detect import detect_objects
from standardphysics_pipeline.discovery.detection_errors import DetectionRateLimited
from standardphysics_pipeline.discovery.detector_transport import RateLimitGate, answer_identity
from standardphysics_pipeline.discovery.discover import _detect_all
from standardphysics_pipeline.discovery.frame_encoding import EncodedFrame

FIREWORKS = "https://api.fireworks.ai/inference/v1"
OPENROUTER = "https://openrouter.ai/api/v1"
ONE_TERMINAL = {"objects": [
    {"name": "payment terminal", "box_2d": [400, 400, 600, 600], "movable": True, "confidence": 0.9},
]}


def reply(objects=ONE_TERMINAL) -> dict:
    return {
        "id": "request-1",
        "choices": [{"message": {"content": json.dumps(objects)}}],
        "usage": {"prompt_tokens": 889, "completion_tokens": 320},
    }


def http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://host/chat", code, "error", headers or {}, io.BytesIO(b""))


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "frame-0001.jpg"
    Image.new("RGB", (64, 48), (120, 90, 60)).save(path)
    return path


@pytest.fixture
def clock(monkeypatch):
    """Time that only moves when something sleeps, and a list of every sleep."""
    state = types.SimpleNamespace(now=1000.0, sleeps=[])

    def sleep(seconds):
        state.sleeps.append(seconds)
        state.now += seconds

    monkeypatch.setattr(detector_transport.time, "sleep", sleep)
    monkeypatch.setattr(detector_transport.time, "monotonic", lambda: state.now)
    monkeypatch.setattr(detector_transport, "RATE_LIMIT_GATE", RateLimitGate())
    return state


def body_for(monkeypatch, base_url: str) -> dict:
    monkeypatch.setenv("DISCOVERY_BASE_URL", base_url)
    return detect._request_body(EncodedFrame(jpeg=b"\xff\xd8", width=64, height=48))


class TestAskingWithoutReasoning:
    def test_fireworks_is_asked_to_skip_reasoning(self, monkeypatch):
        body = body_for(monkeypatch, FIREWORKS)
        assert body["reasoning_effort"] == "none"
        assert "provider" not in body

    def test_openrouter_is_never_sent_a_reasoning_switch(self, monkeypatch):
        body = body_for(monkeypatch, OPENROUTER)
        assert "reasoning_effort" not in body and "reasoning" not in body
        assert body["provider"] == detector_transport.PROVIDER_ROUTING

    def test_an_unknown_host_gets_neither_field(self, monkeypatch):
        body = body_for(monkeypatch, "https://models.example.com/v1")
        assert not {"reasoning_effort", "reasoning", "provider"} & body.keys()

    def test_an_answer_given_with_reasoning_is_not_served_as_one_without(self, monkeypatch, photo, tmp_path):
        monkeypatch.setenv("DISCOVERY_MODEL", "accounts/fireworks/models/detector")
        monkeypatch.setenv("DISCOVERY_BASE_URL", OPENROUTER)
        with_reasoning = answer_identity()
        DetectionCache(tmp_path / "cache", with_reasoning).put(photo, [])
        monkeypatch.setenv("DISCOVERY_BASE_URL", FIREWORKS)
        without_reasoning = answer_identity()
        assert without_reasoning != with_reasoning
        assert DetectionCache(tmp_path / "cache", without_reasoning).get(photo, "frame-0001") is None
        assert DetectionCache(tmp_path / "cache", with_reasoning).get(photo, "frame-0001") == []


class TestRateLimits:
    def test_the_wait_the_host_asks_for_is_honoured(self, photo, clock):
        answers = [http_error(429, {"Retry-After": "7"}), reply()]

        def transport(url, body, headers):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        found = detect_objects(photo, "frame-0001", transport=transport)
        assert [one.name for one in found] == ["payment terminal"]
        assert clock.sleeps == [pytest.approx(7.0)]

    def test_a_rate_limit_outlasts_the_attempts_a_server_error_gets(self, photo, clock):
        limited = detector_transport.MAX_ATTEMPTS + 2
        calls = []

        def transport(url, body, headers):
            calls.append(1)
            if len(calls) <= limited:
                raise http_error(429)
            return reply()

        assert len(detect_objects(photo, "frame-0001", transport=transport)) == 1
        assert len(calls) == limited + 1

    def test_a_rate_limit_that_never_lifts_still_raises(self, photo, clock):
        def transport(url, body, headers):
            raise http_error(429, {"Retry-After": "1"})

        with pytest.raises(DetectionRateLimited):
            detect_objects(photo, "frame-0001", transport=transport)
        assert len(clock.sleeps) == detector_transport.RATE_LIMITED_ATTEMPTS - 1

    def test_one_rate_limit_holds_every_request_behind_it(self, clock):
        gate = RateLimitGate()
        gate.hold(5.0)
        gate.wait()
        gate.wait()
        assert sum(clock.sleeps) == pytest.approx(5.0)

    def test_retry_after_as_a_date_is_read(self, monkeypatch):
        monkeypatch.setattr(detector_transport.time, "time", lambda: 1_700_000_000.0)
        assert detector_transport._retry_after({"Retry-After": "Tue, 14 Nov 2023 22:13:30 GMT"}) == pytest.approx(10.0)
        assert detector_transport._retry_after({}) is None
        assert detector_transport._retry_after({"Retry-After": "soon"}) is None


class TestNoFrameIsSilentlyDropped:
    def frame(self, frame_id: str):
        return types.SimpleNamespace(frame_id=frame_id)

    def test_a_frame_the_first_pass_lost_is_asked_for_again(self, photo, clock):
        calls = []

        def transport(url, body, headers):
            calls.append(1)
            if len(calls) <= detector_transport.MAX_ATTEMPTS:
                raise http_error(503)
            return reply()

        detections, failures = _detect_all(
            [self.frame("frame-0001")], {"frame-0001": photo}, transport, None, {},
        )
        assert failures == []
        assert [one.name for one in detections["frame-0001"]] == ["payment terminal"]

    def test_a_frame_that_fails_both_passes_is_reported(self, photo, clock):
        def transport(url, body, headers):
            raise http_error(503)

        detections, failures = _detect_all(
            [self.frame("frame-0001")], {"frame-0001": photo}, transport, None, {},
        )
        assert detections == {}
        assert len(failures) == 1 and failures[0].startswith("frame-0001:")
