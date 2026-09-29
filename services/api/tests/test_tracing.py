"""Weave comes up with the server, or the server comes up without it.

The traces are the record of what a run did, so the wiring is tested rather
than watched once by eye. CI has no keys, so the account is stood in for.
"""

import json
import os
import sys
import textwrap
import threading
import time

import child_stages
import hanging_child
import pytest
from evidence_uploads import complete_geometry, complete_semantics
from fastapi.testclient import TestClient
from standardphysics_agents import tracing

from conftest import create_scan, drain, no_blender_stages, sign_up
from standardphysics_api.app import create_app
from standardphysics_api.settings import Settings
from standardphysics_api.worker_child import in_own_process


class FakeWeave:
    """Enough of the Weave surface to prove startup reached it."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.projects: list[str] = []

    def init(self, project: str, settings: dict | None = None) -> None:
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

    def init(project, settings=None):
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
        complete_geometry(client, scan_id)
        complete_semantics(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)
    in_child = [(call["call"], call.get("name")) for call in _calls_from_other_processes(recording_weave)]
    assert ("init", None) in in_child
    assert ("op", "child_stages.label") in in_child
    assert in_child[-1] == ("finish", None)


class StalledWeave(FakeWeave):
    """A W&B endpoint that accepts the connection and never answers."""

    def __init__(self) -> None:
        super().__init__()
        self.answer = threading.Event()

    def init(self, project: str, settings: dict | None = None) -> None:
        self.answer.wait()

    def finish(self) -> None:
        self.answer.wait()


def test_a_weave_that_never_answers_does_not_hold_up_startup(tmp_path, monkeypatch):
    stalled = StalledWeave()
    monkeypatch.setitem(sys.modules, "weave", stalled)
    monkeypatch.setenv("SP_WEAVE_INIT_TIMEOUT_SECONDS", "0.3")
    started = time.monotonic()
    try:
        with serve(tmp_path, weave_project="physics") as client:
            assert time.monotonic() - started < 10
            tracing_report = client.get("/health/details").json()["tracing"]
    finally:
        stalled.answer.set()
    assert tracing_report["active"] is False
    assert "did not finish within 0.3 s" in tracing_report["off_because"]


def test_a_child_whose_flush_never_returns_still_exits_and_reports(monkeypatch):
    """The real SDK also waits for its queue at exit, without a limit, so the child must not wait on it."""
    monkeypatch.setenv("SP_WEAVE_FLUSH_TIMEOUT_SECONDS", "0.5")
    started = time.monotonic()
    assert in_own_process(hanging_child.trace_through_a_stalled_flush, "physics", timeout_seconds=20) == "finished"
    assert time.monotonic() - started < 20
