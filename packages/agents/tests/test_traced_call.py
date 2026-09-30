"""`traced_call` names a piece of work in the trace without logging its real arguments, and once tracing is up the
pipeline's model calls go through it."""

import sys

import pytest
from standardphysics_agents import tracing
from standardphysics_pipeline import model_calls


class RecordingWeave:
    """Enough of Weave to see which ops were made and called."""

    def __init__(self) -> None:
        self.ops: list[str] = []
        self.calls: list[tuple[str, tuple]] = []

    def init(self, project: str, settings: dict | None = None) -> None:
        return None

    def finish(self) -> None:
        return None

    def op(self, *args, name=None, **kwargs):
        if args:
            raise TypeError("this version takes name= and returns a decorator")

        def wrap(fn):
            self.ops.append(name)

            def call(*call_args, **call_kwargs):
                self.calls.append((name, call_args))
                return fn(*call_args, **call_kwargs)

            return call

        return wrap


@pytest.fixture
def weave(monkeypatch):
    fake = RecordingWeave()
    monkeypatch.setitem(sys.modules, "weave", fake)
    monkeypatch.setenv("WANDB_PROJECT", "standardphysics")
    monkeypatch.setattr(tracing, "_TRACING", tracing._Tracing())
    monkeypatch.setattr(model_calls, "_reporter", model_calls._unreported)
    yield fake
    tracing.shutdown()


def test_each_name_is_its_own_call_in_the_trace(weave):
    tracing.init()
    assert tracing.traced_call("job.process", {"scan_id": "a"}, lambda: True) is True
    assert tracing.traced_call("model.choose", {"model": "m"}, lambda: "reply") == "reply"
    tracing.traced_call("job.process", {"scan_id": "b"}, lambda: True)
    assert weave.ops == ["job.process", "model.choose"]
    assert [name for name, _ in weave.calls] == ["job.process", "model.choose", "job.process"]


def test_only_the_given_inputs_are_logged_and_the_work_by_its_type():
    inputs, run = {"model": "m", "host": "openrouter.ai"}, lambda: "reply"
    assert tracing.as_logged(inputs) == inputs
    assert tracing.as_logged(run) == "<function>"


def test_tracing_coming_up_traces_the_pipelines_model_calls(weave):
    assert tracing.init()
    assert model_calls.reported("model.detect", {"images": 1}, lambda: {"objects": []}) == {"objects": []}
    assert [name for name, _ in weave.calls] == ["model.detect"]


def test_without_a_project_the_pipelines_calls_stay_unreported(monkeypatch):
    monkeypatch.delenv("WANDB_PROJECT", raising=False)
    monkeypatch.setattr(tracing, "_TRACING", tracing._Tracing())
    monkeypatch.setattr(model_calls, "_reporter", model_calls._unreported)
    assert not tracing.init()
    assert model_calls._reporter is model_calls._unreported
