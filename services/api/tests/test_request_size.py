"""Ordinary request bodies are capped before they are read; artifact uploads keep their own streaming cap."""

import json

import pytest

from conftest import create_scan, put_artifact
from standardphysics_api import auth, request_size

LIMIT = 4096


@pytest.fixture
def unparsed_sign_ins(monkeypatch):
    """Every sign-in the handler got as far as checking. A body refused for its size must never get here."""
    checked: list[str] = []

    def refuse_to_check(database, body, address, limits):
        checked.append(body.email)
        raise AssertionError("an oversized body reached the sign-in handler")

    monkeypatch.setattr(auth, "_authenticate", refuse_to_check)
    return checked


def _oversized_sign_in() -> bytes:
    return json.dumps({"email": "owner@example.com", "password": "x" * (LIMIT * 2)}).encode()


def test_an_oversized_sign_in_is_refused_by_its_declared_length(make_client, unparsed_sign_ins):
    with make_client(sign_in_as_owner=False, max_request_body_bytes=LIMIT) as browser:
        refused = browser.post(
            "/api/auth/sign-in", content=_oversized_sign_in(), headers={"Content-Type": "application/json"}
        )
    assert refused.status_code == 413, refused.text
    assert "too large" in refused.json()["error"]
    assert unparsed_sign_ins == []


def test_an_oversized_chunked_sign_in_with_no_length_is_cut_off(make_client, unparsed_sign_ins):
    body = _oversized_sign_in()
    chunks = (body[offset:offset + 1000] for offset in range(0, len(body), 1000))
    with make_client(sign_in_as_owner=False, max_request_body_bytes=LIMIT) as browser:
        refused = browser.post("/api/auth/sign-in", content=chunks, headers={"Content-Type": "application/json"})
    assert "content-length" not in refused.request.headers
    assert refused.status_code == 413, refused.text
    assert unparsed_sign_ins == []


def test_a_sign_in_under_the_cap_is_answered_as_usual(make_client):
    with make_client(max_request_body_bytes=LIMIT) as browser:
        signed = browser.post("/api/auth/sign-in", json={"email": "owner@example.com", "password": "wrong-password"})
    assert signed.status_code == 401


@pytest.mark.parametrize(("method", "path", "streamed"), [
    ("PUT", "/api/scans/0f0f/artifacts/lidar-mesh", True),
    ("PUT", "/api/scans/0f0f/requests/door-width/photo", True),
    ("POST", "/api/scans/0f0f/artifacts/lidar-mesh", False),
    ("PUT", "/api/scans/0f0f/requests/door-width/answer", False),
    ("POST", "/api/auth/sign-in", False),
])
def test_only_the_uploads_that_stream_to_disk_skip_the_cap(method, path, streamed):
    assert request_size._streams_to_disk({"method": method, "path": path}) is streamed


def test_an_artifact_far_over_the_json_cap_still_uploads(make_client):
    with make_client(max_request_body_bytes=LIMIT) as phone:
        scan_id = create_scan(phone)
        room = b" " * (LIMIT * 50) + b"{}"
        uploaded = put_artifact(phone, scan_id, "big-room", room, "room_json")
    assert uploaded.status_code == 201, uploaded.text
