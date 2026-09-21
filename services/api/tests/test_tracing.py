"""Weave comes up with the server, or the server comes up without it.

The traces are the record of what a run did, so the wiring is tested rather
than watched once by eye. CI has no keys, so the account is stood in for.
"""

import sys

import pytest
from fastapi.testclient import TestClient
from standardphysics_agents import tracing

from conftest import no_blender_stages, sign_up
from standardphysics_api.app import create_app
from standardphysics_api.settings import Settings


class FakeWeave:
    """Enough of the Weave surface to prove startup reached it."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.projects: list[str] = []

    def init(self, project: str) -> None:
        if self.failure is not None:
            raise self.failure
        self.projects.append(project)

    def op(self, *args, **kwargs):
        return args[0] if args else (lambda fn: fn)


@pytest.fixture
def weave(monkeypatch):
    fake = FakeWeave()
    monkeypatch.setitem(sys.modules, "weave", fake)
    yield fake
    tracing.shutdown()


def serve(tmp_path, **settings) -> TestClient:
    options = dict(data_dir=tmp_path / "var", seed_sample_shop=False, **settings)
    return TestClient(create_app(Settings(**options), no_blender_stages(), run_worker=False))


def test_a_project_turns_tracing_on(tmp_path, weave):
    with serve(tmp_path, weave_project="physics", weave_entity="physics") as client:
        sign_up(client)
        assert client.get("/api/scans").status_code == 200
        assert weave.projects == ["physics/physics"]
        assert tracing.project_url() == "https://wandb.ai/physics/physics/weave"


def test_it_stops_when_the_server_stops(tmp_path, weave):
    with serve(tmp_path, weave_project="physics"):
        pass
    assert not tracing.is_live()


def test_no_project_leaves_it_off(tmp_path, weave):
    with serve(tmp_path) as client:
        sign_up(client)
        assert client.get("/api/scans").status_code == 200
        assert weave.projects == []
        assert not tracing.is_live()


def test_a_key_weave_rejects_does_not_stop_the_server(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "weave", FakeWeave(failure=ValueError("api_key not valid")))
    with serve(tmp_path, weave_project="physics") as client:
        sign_up(client)
        assert client.get("/api/scans").status_code == 200
        assert not tracing.is_live()


def test_the_environment_names_the_project(monkeypatch):
    monkeypatch.setenv("WANDB_PROJECT", "physics")
    monkeypatch.setenv("WANDB_ENTITY", "physics")
    settings = Settings.from_environment()
    assert (settings.weave_project, settings.weave_entity) == ("physics", "physics")
