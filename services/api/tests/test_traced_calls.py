"""What the API sends to Weave: each model call without its key, one root call per job, and one drag check in
DRAG_CHECKS_PER_TRACE."""

import contextlib
import itertools
import json

from standardphysics_fixtures import node_id
from test_model_chooser import _capture_requests

from conftest import drain
from standardphysics_api import layout, model_chooser, worker_handlers
from standardphysics_api.model_chooser import ModelChooser


def _recording(monkeypatch, module):
    seen = []

    def traced_call(name, inputs, run):
        seen.append((name, inputs))
        return run()

    monkeypatch.setattr(module, "traced_call", traced_call)
    return seen


def test_a_model_call_is_traced_without_its_key_or_address(monkeypatch):
    _capture_requests(monkeypatch)
    seen = _recording(monkeypatch, model_chooser)
    chooser = ModelChooser("https://openrouter.ai/api/v1", "moonshotai/kimi-k3", api_key="sk-or-secret")
    assert chooser.ask([{"role": "user", "content": "pick"}]) == '{"choose": [1]}'
    assert [name for name, _ in seen] == ["model.choose"]
    logged = json.dumps(seen[0][1])
    assert seen[0][1]["host"] == "openrouter.ai" and seen[0][1]["model"] == "moonshotai/kimi-k3"
    assert "sk-or-secret" not in logged and "/api/v1" not in logged


def test_every_job_is_one_root_call_named_for_its_kind(make_client, monkeypatch):
    seen = _recording(monkeypatch, worker_handlers)
    with make_client(seed=True) as client:
        drain(client)
    names = {name for name, _ in seen}
    assert {"job.assess", "job.display"} <= names
    assert all(name.startswith("job.") for name in names)
    assert all(set(inputs) == {"job_id", "scan_id", "revision"} for _, inputs in seen)


def test_one_drag_check_in_twenty_is_traced(make_client, monkeypatch):
    suspended = []

    @contextlib.contextmanager
    def counted_suspension():
        suspended.append(1)
        yield

    monkeypatch.setattr(layout, "_drag_checks", itertools.count())
    monkeypatch.setattr(layout, "suspend_tracing", counted_suspension)
    still = {"node_id": str(node_id("case_east")), "delta_translation": {"x": 0.0, "y": 0.0, "z": 0.0},
             "delta_rotation_z_degrees": 0.0}
    with make_client(seed=True) as client:
        drain(client)
        scan_id = client.get("/api/scans").json()["scans"][0]["id"]
        checks = layout.DRAG_CHECKS_PER_TRACE + 1
        for sequence in range(checks):
            body = {"base_revision": 0, "sequence": sequence, "moves": [still]}
            assert client.post(f"/api/scans/{scan_id}/layout-checks", json=body).status_code == 200
    assert len(suspended) == checks - 2
