"""Weave comes up with the server, or the server comes up without it.

The traces are the record of what a run did, so the wiring is tested rather
than watched once by eye. CI has no keys, so the account is stood in for.
"""

import json
import os
import sys
import textwrap

import child_stages
import pytest
from fastapi.testclient import TestClient
from standardphysics_agents import tracing
from test_job_lifecycle import _complete_geometry, _complete_semantics

from conftest import create_scan, drain, no_blender_stages, sign_up
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


RECORDING_WEAVE = textwrap.dedent("""
    import json
    import os

    def _record(**call):
        with open(os.environ["SP_WEAVE_CALLS"], "a") as calls:
            calls.write(json.dumps({"pid": os.getpid(), **call}) + "\\n")

    def init(project):
        _record(call="init", project=project)

    def finish():
        _record(call="finish")

    def op(name=None):
        def wrap(fn):
            def run(*args, **kwargs):
                _record(call="op", name=name)
                return fn(*args, **kwargs)
            return run
        return wrap
""")


@pytest.fixture
def recording_weave(tmp_path, monkeypatch):
    """A weave module on the path every process imports from, writing each call it gets to a file."""
    package = tmp_path / "recording-weave"
    package.mkdir()
    (package / "weave.py").write_text(RECORDING_WEAVE)
    calls = tmp_path / "weave-calls.jsonl"
    monkeypatch.setenv("SP_WEAVE_CALLS", str(calls))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(package), os.environ.get("PYTHONPATH", "")]))
    monkeypatch.syspath_prepend(str(package))
    monkeypatch.delitem(sys.modules, "weave", raising=False)
    yield calls
    tracing.shutdown()


def _calls_from_other_processes(calls) -> list[dict]:
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    return [call for call in recorded if call["pid"] != os.getpid()]


def test_a_job_run_in_its_own_process_sends_its_traces(make_client, recording_weave):
    client = make_client(jobs_in_own_process=True, weave_project="physics", evidence_settle_seconds=0.0)
    client.app.state.worker.stages_in_child = child_stages.traced_at_labeling
    with client:
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        _complete_semantics(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)
    in_child = [(call["call"], call.get("name")) for call in _calls_from_other_processes(recording_weave)]
    assert ("init", None) in in_child
    assert ("op", "child_stages.label") in in_child
    assert in_child[-1] == ("finish", None)
