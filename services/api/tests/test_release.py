"""/health/details names the commit the running image was built from, so a rollback knows where it is going from."""

from __future__ import annotations

from standardphysics_api.settings import Settings

COMMIT = "9357a13e0f1c2b4d5a6978a0b1c2d3e4f5a6b7c8"


def test_details_name_the_commit_the_image_was_built_from(make_client):
    with make_client(git_sha=COMMIT) as client:
        assert client.get("/health/details").json()["commit"] == COMMIT


def test_the_commit_comes_from_the_image_environment(monkeypatch):
    monkeypatch.setenv("SP_GIT_SHA", COMMIT)
    assert Settings.from_environment().git_sha == COMMIT


def test_a_server_built_outside_the_image_says_it_does_not_know(monkeypatch):
    monkeypatch.delenv("SP_GIT_SHA", raising=False)
    assert Settings.from_environment().git_sha == "unknown"
