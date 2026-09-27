"""Tracing, with and without an account.

Every check and every agent call carries `@traced`, so the two things that
matter are that it does nothing at all when Weave is not configured — CI has no
keys — and that it really does route through `weave.op` when it is. The second
half has never been true in CI, so it is tested against a stand-in module
rather than assumed.
"""

from __future__ import annotations

import builtins
import sys

import pytest
from standardphysics_agents import tracing


class FakeWeave:
    """Enough of the Weave surface to prove the wiring."""

    def __init__(self, style: str = "factory") -> None:
        self.style = style
        self.projects: list[str] = []
        self.ops: list[str] = []
        self.calls: list[str] = []

    def init(self, project: str) -> None:
        self.projects.append(project)

    def op(self, *args, **kwargs):
        """Both spellings Weave has used, so the wrapper survives either.

        The mismatched call raises `TypeError`, which is what Python raises for
        a missing positional argument and what the wrapper watches for.
        """
        name = kwargs.get("name")
        if self.style == "factory":
            if args:
                raise TypeError("this version takes name= and returns a decorator")
            return lambda fn: self._wrap(name, fn)
        if not args:
            raise TypeError("op() missing 1 required positional argument: 'fn'")
        return self._wrap(name, args[0])

    def _wrap(self, name, fn):
        self.ops.append(name)

        def traced_call(*args, **kwargs):
            self.calls.append(name)
            return fn(*args, **kwargs)

        return traced_call


@pytest.fixture
def weave(monkeypatch):
    fake = FakeWeave()
    monkeypatch.setitem(sys.modules, "weave", fake)
    monkeypatch.setenv("WANDB_PROJECT", "standardphysics")
    monkeypatch.delenv("WANDB_ENTITY", raising=False)
    yield fake
    tracing.shutdown()


@pytest.fixture(autouse=True)
def off():
    tracing.shutdown()
    yield
    tracing.shutdown()


class TestWithoutAnAccount:
    def test_no_project_means_no_tracing(self, monkeypatch):
        monkeypatch.delenv("WANDB_PROJECT", raising=False)
        assert tracing.init() is False
        assert not tracing.is_live()

    def test_a_traced_function_still_runs(self):
        @tracing.traced("test.plain")
        def double(value: int) -> int:
            return value * 2

        assert double(21) == 42

    def test_there_is_no_project_url(self):
        assert tracing.project_url() is None

    def test_a_missing_weave_install_is_not_an_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "weave", None)
        monkeypatch.setenv("WANDB_PROJECT", "standardphysics")
        assert tracing.init() is False

    def test_a_missing_weave_install_is_announced_when_a_project_is_set(self, monkeypatch, caplog):
        """Production sets WANDB_PROJECT expecting traces. An image built
        without the observability extra must say so, not go quiet."""
        monkeypatch.setitem(sys.modules, "weave", None)
        monkeypatch.setenv("WANDB_PROJECT", "standardphysics")
        with caplog.at_level("WARNING", logger=tracing.__name__):
            assert tracing.init() is False
        assert "observability" in caplog.text
        assert "standardphysics" in caplog.text

    def test_a_weave_that_breaks_on_import_is_announced(self, monkeypatch, caplog):
        real_import = builtins.__import__

        def import_with_broken_weave(name, *args, **kwargs):
            if name == "weave":
                raise RuntimeError("incompatible protobuf")
            return real_import(name, *args, **kwargs)

        monkeypatch.delitem(sys.modules, "weave", raising=False)
        monkeypatch.setattr(builtins, "__import__", import_with_broken_weave)
        monkeypatch.setenv("WANDB_PROJECT", "standardphysics")
        with caplog.at_level("WARNING", logger=tracing.__name__):
            assert tracing.init() is False
        assert "incompatible protobuf" in caplog.text

    def test_a_key_without_a_project_is_announced(self, monkeypatch, caplog):
        monkeypatch.delenv("WANDB_PROJECT", raising=False)
        monkeypatch.setenv("WANDB_API_KEY", "not-a-real-key")
        with caplog.at_level("WARNING", logger=tracing.__name__):
            assert tracing.init() is False
        assert "WANDB_PROJECT" in caplog.text

    def test_no_key_and_no_project_stays_quiet(self, monkeypatch, caplog):
        monkeypatch.delenv("WANDB_PROJECT", raising=False)
        monkeypatch.delenv("WANDB_API_KEY", raising=False)
        with caplog.at_level("WARNING", logger=tracing.__name__):
            assert tracing.init() is False
        assert caplog.text == ""


class TestWithAnAccount:
    def test_init_brings_it_up(self, weave):
        assert tracing.init() is True
        assert tracing.is_live()
        assert weave.projects == ["standardphysics"]

    def test_the_entity_joins_the_project(self, weave, monkeypatch):
        monkeypatch.setenv("WANDB_ENTITY", "aria")
        assert tracing.init()
        assert weave.projects == ["aria/standardphysics"]

    def test_a_project_given_directly_wins(self, weave):
        assert tracing.init(project="scratch")
        assert weave.projects == ["scratch"]

    def test_a_traced_call_goes_through_weave(self, weave):
        @tracing.traced("test.counted")
        def double(value: int) -> int:
            return value * 2

        tracing.init()
        assert double(21) == 42
        assert weave.calls == ["test.counted"]

    def test_the_call_is_named_in_the_trace(self, weave):
        @tracing.traced("check.route_clear_width")
        def measure() -> int:
            return 31

        tracing.init()
        measure()
        assert weave.ops == ["check.route_clear_width"]

    def test_it_wraps_once_however_often_it_is_called(self, weave):
        @tracing.traced("test.once")
        def noop() -> None:
            return None

        tracing.init()
        for _ in range(5):
            noop()
        assert weave.ops == ["test.once"]
        assert len(weave.calls) == 5

    def test_the_other_weave_signature_works_too(self, monkeypatch):
        """`weave.op` has been both a decorator and a decorator factory."""
        fake = FakeWeave(style="plain")
        monkeypatch.setitem(sys.modules, "weave", fake)
        monkeypatch.setenv("WANDB_PROJECT", "standardphysics")

        @tracing.traced("test.either_way")
        def double(value: int) -> int:
            return value * 2

        assert tracing.init()
        assert double(21) == 42
        assert fake.calls == ["test.either_way"]

    def test_the_project_url_points_at_the_traces(self, weave):
        tracing.init()
        assert tracing.project_url() == "https://wandb.ai/standardphysics/weave"

    def test_shutting_down_stops_it(self, weave):
        @tracing.traced("test.stopped")
        def noop() -> None:
            return None

        tracing.init()
        noop()
        tracing.shutdown()
        noop()
        assert len(weave.calls) == 1


class TestEverythingIsTraced:
    """Plan section 8: @weave.op on every agent call and every check."""

    def _named(self, fn) -> str | None:
        return getattr(fn, "traced_name", None)

    def test_every_check_in_the_registry(self):
        from standardphysics_agents.checks import REGISTRY

        for _, check in REGISTRY:
            assert self._named(check), check.__name__

    def test_every_check_is_named_as_one(self):
        from standardphysics_agents.checks import REGISTRY

        for _, check in REGISTRY:
            assert self._named(check).startswith("check")

    def test_the_run_that_holds_them(self):
        from standardphysics_agents.checks import run_checks

        assert self._named(run_checks) == "checks.run"

    def test_the_pass_over_a_shop(self):
        from standardphysics_agents import assess

        assert self._named(assess) == "assess"

    def test_every_executor_in_the_ask_box(self):
        from standardphysics_agents.ask import EXECUTORS, ask

        for kind, executor in EXECUTORS.items():
            assert self._named(executor), kind
        assert self._named(ask) == "ask"

    def test_both_routers(self):
        from standardphysics_agents.router import LocalPolicyRouter, TypeSafeRouter

        assert self._named(TypeSafeRouter.decide) == "router.typesafe"
        assert self._named(LocalPolicyRouter.decide) == "router.local_policy"

    def test_every_model_call(self):
        from standardphysics_agents.models import OpenRouter

        assert self._named(OpenRouter.structured) == "model.openrouter"

    def test_the_fix_agent(self):
        from standardphysics_agents.fix import propose_fix

        assert self._named(propose_fix) == "fix.propose"

    def test_every_branch_of_the_loop(self):
        from standardphysics_agents.loop import HANDLERS, run_loop, run_pass

        for action, handler in HANDLERS.items():
            assert self._named(handler), action
        assert self._named(run_pass) == "loop.pass"
        assert self._named(run_loop) == "loop.run"

    def test_the_evaluation_and_every_case_in_it(self):
        from standardphysics_agents.evaluation import evaluate, run_case

        assert self._named(evaluate) == "evaluation.run"
        assert self._named(run_case) == "evaluation.case"

    def test_the_whole_loop_reads_as_one_tree(self, weave):
        """Names are dotted and share a prefix, so a trace nests under one
        heading rather than going flat."""
        from standardphysics_agents.checks import REGISTRY, run_checks

        names = [self._named(run_checks), *[self._named(c) for _, c in REGISTRY]]
        assert all("." in name for name in names)
        assert {name.split(".")[0] for name in names} == {"checks"}
