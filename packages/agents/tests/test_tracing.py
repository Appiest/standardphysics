"""Tracing, with and without an account.

Every check and every agent call carries `@traced`, so the two things that
matter are that it does nothing at all when Weave is not configured — CI has no
keys — and that it really does route through `weave.op` when it is. The second
half has never been true in CI, so it is tested against a stand-in module
rather than assumed.
"""

from __future__ import annotations

import builtins
import logging
import sys
import threading
import time

import pytest
from standardphysics_agents import tracing


class FakeWeave:
    """Enough of the Weave surface to prove the wiring."""

    def __init__(self, style: str = "factory") -> None:
        self.style = style
        self.projects: list[str] = []
        self.settings: list[dict | None] = []
        self.ops: list[str] = []
        self.calls: list[str] = []
        self.flushes = 0

    def init(self, project: str, settings: dict | None = None) -> None:
        self.projects.append(project)
        self.settings.append(settings)

    def finish(self) -> None:
        self.flushes += 1

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
def off(monkeypatch):
    tracing.shutdown()
    monkeypatch.setattr(tracing, "_TRACING", tracing._Tracing())
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


class TestStatusAndShutdown:
    def test_the_status_says_why_tracing_is_off(self, monkeypatch):
        monkeypatch.delenv("WANDB_PROJECT", raising=False)
        tracing.init()
        assert tracing.tracing_status() == {
            "active": False, "project_url": None, "off_because": "WANDB_PROJECT is not set",
            "delivery_errors": 0, "last_delivery_error": None,
        }

    def test_the_status_names_a_failed_init(self, monkeypatch):
        def reject(project: str) -> None:
            raise RuntimeError("401 bad key")

        broken = FakeWeave()
        broken.init = reject
        monkeypatch.setitem(sys.modules, "weave", broken)
        monkeypatch.setenv("WANDB_PROJECT", "standardphysics")
        tracing.init()
        assert tracing.tracing_status()["active"] is False
        assert "weave.init failed" in tracing.tracing_status()["off_because"]

    def test_the_status_points_at_live_traces(self, weave):
        tracing.init()
        assert tracing.tracing_status() == {
            "active": True, "project_url": "https://wandb.ai/standardphysics/weave", "off_because": None,
            "delivery_errors": 0, "last_delivery_error": None,
        }

    def test_shutting_down_flushes_queued_traces_once(self, weave):
        tracing.init()
        tracing.shutdown()
        tracing.shutdown()
        assert weave.flushes == 1

    def test_a_flush_that_fails_does_not_stop_the_shutdown(self, weave, caplog):
        def refuse() -> None:
            raise ConnectionError("no network")

        weave.finish = refuse
        tracing.init()
        with caplog.at_level("WARNING", logger=tracing.__name__):
            tracing.shutdown()
        assert not tracing.is_live()
        assert "no network" in caplog.text

    def test_a_flush_that_raises_is_counted_but_not_abandoned(self, weave):
        def refuse() -> None:
            raise ConnectionError("no network")

        weave.finish = refuse
        tracing.init()
        tracing.shutdown()
        status = tracing.tracing_status()
        assert status["delivery_errors"] == 1
        assert status["last_delivery_error"] == "weave could not flush: no network"
        assert not tracing.flush_was_abandoned()

    def test_a_process_that_exits_without_shutting_down_still_flushes(self, weave, monkeypatch):
        registered: list = []
        monkeypatch.setattr(tracing.atexit, "register", registered.append)
        monkeypatch.setattr(tracing, "_TRACING", tracing._Tracing())
        tracing.init()
        tracing.init()
        assert len(registered) == 1
        registered[0]()
        assert weave.flushes == 1


class StalledWeave(FakeWeave):
    """A W&B endpoint that accepts the connection and never answers."""

    def __init__(self, stall_init: bool = False, stall_finish: bool = False) -> None:
        super().__init__()
        self.stall_init, self.stall_finish = stall_init, stall_finish
        self.answer = threading.Event()

    def init(self, project: str, settings: dict | None = None) -> None:
        if self.stall_init:
            self.answer.wait()
        super().init(project)

    def finish(self) -> None:
        if self.stall_finish:
            self.answer.wait()
        super().finish()


@pytest.fixture
def stalled(monkeypatch):
    """Deadlines of a fifth of a second, and a Weave told which calls to hang on."""
    monkeypatch.setenv("SP_WEAVE_INIT_TIMEOUT_SECONDS", "0.2")
    monkeypatch.setenv("SP_WEAVE_FLUSH_TIMEOUT_SECONDS", "0.2")
    monkeypatch.setenv("WANDB_PROJECT", "standardphysics")
    installed: list[StalledWeave] = []

    def install(**stalls: bool) -> StalledWeave:
        fake = StalledWeave(**stalls)
        monkeypatch.setitem(sys.modules, "weave", fake)
        installed.append(fake)
        return fake

    yield install
    for fake in installed:
        fake.answer.set()


def _seconds(work) -> float:
    started = time.monotonic()
    work()
    return time.monotonic() - started


class TestAStalledEndpoint:
    """A W&B endpoint that stops answering may cost startup or a child's exit its deadline, never more."""

    def test_an_init_that_never_returns_leaves_tracing_off_at_its_deadline(self, stalled):
        stalled(stall_init=True)
        assert _seconds(lambda: tracing.init()) < 2
        assert not tracing.is_live()

    def test_the_status_says_init_ran_out_of_time(self, stalled):
        stalled(stall_init=True)
        tracing.init()
        status = tracing.tracing_status()
        assert status["active"] is False
        assert status["off_because"] == "weave.init for standardphysics did not finish within 0.2 s"

    def test_a_traced_call_after_a_stalled_init_calls_straight_through(self, stalled):
        fake = stalled(stall_init=True)

        @tracing.traced("test.after_stall")
        def double(value: int) -> int:
            return value * 2

        tracing.init()
        fake.answer.set()
        assert double(21) == 42
        assert fake.calls == []

    def test_a_flush_that_never_returns_lets_the_shutdown_finish(self, stalled, caplog):
        stalled(stall_finish=True)
        assert tracing.init()
        with caplog.at_level("WARNING", logger=tracing.__name__):
            assert _seconds(tracing.shutdown) < 2
        assert not tracing.is_live()
        assert "within 0.2 s" in caplog.text

    def test_a_flush_that_ran_out_of_time_is_counted_and_remembered(self, stalled):
        stalled(stall_finish=True)
        tracing.init()
        tracing.shutdown()
        assert tracing.tracing_status()["delivery_errors"] == 1
        assert tracing.tracing_status()["last_delivery_error"] == "weave did not flush within 0.2 s"
        assert tracing.flush_was_abandoned()

    def test_a_flush_that_finishes_is_not_abandoned(self, stalled):
        stalled()
        tracing.init()
        tracing.shutdown()
        assert not tracing.flush_was_abandoned()
        assert tracing.tracing_status()["delivery_errors"] == 0

    def test_a_process_block_with_a_stalled_flush_still_ends(self, stalled):
        stalled(stall_finish=True)

        def traced_block() -> None:
            with tracing.tracing_for_this_process() as started:
                assert started

        assert _seconds(traced_block) < 2

    def test_a_deadline_that_is_not_a_number_falls_back_to_the_default(self, monkeypatch, caplog):
        monkeypatch.setenv("SP_WEAVE_INIT_TIMEOUT_SECONDS", "soon")
        with caplog.at_level("WARNING", logger=tracing.__name__):
            assert tracing._deadline("SP_WEAVE_INIT_TIMEOUT_SECONDS", 30.0) == 30.0
        assert "SP_WEAVE_INIT_TIMEOUT_SECONDS" in caplog.text


class TestDeliveryFailures:
    """The SDK logs a batch it could not send and moves on; that log is all it surfaces."""

    SENDER = "weave.trace_server_bindings.http_utils"

    def test_a_dropped_batch_the_sdk_logs_is_counted(self, weave):
        tracing.init()
        logging.getLogger(self.SENDER).error("Error sending batch of %s call events to server", 3)
        status = tracing.tracing_status()
        assert status["delivery_errors"] == 1
        assert status["last_delivery_error"] == "Error sending batch of 3 call events to server"

    def test_a_warning_is_not_a_delivery_failure(self, weave):
        tracing.init()
        logging.getLogger(self.SENDER).warning("Batch processing failed, processing items individually")
        assert tracing.tracing_status()["delivery_errors"] == 0

    def test_nothing_is_counted_while_tracing_is_off(self, weave):
        tracing.init()
        tracing.shutdown()
        logging.getLogger(self.SENDER).error("Error sending batch of 1 call events to server")
        assert tracing.tracing_status()["delivery_errors"] == 0


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


class FakeSpan:
    """A conversation, turn, tool or model call, and what was written to it."""

    def __init__(self, sdk, kind, fields) -> None:
        self.sdk, self.kind, self.fields = sdk, kind, fields
        self.result = None
        self.recorded = None

    def __enter__(self):
        self.sdk.opened.append(self.kind)
        self.sdk.current[self.kind] = self
        return self

    def __exit__(self, kind, error, traceback):
        self.sdk.closed.append((self.kind, kind))
        self.sdk.current.pop(self.kind, None)
        return False

    def start_turn(self, **fields):
        return FakeSpan(self.sdk, "turn", fields)

    def start_tool(self, **fields):
        return FakeSpan(self.sdk, "tool", fields)

    def start_llm(self, **fields):
        return FakeSpan(self.sdk, "llm", fields)

    def record(self, **fields):
        self.recorded = fields


class FakeConversationModule:
    def __init__(self, sdk) -> None:
        self.sdk = sdk

    def get_current_conversation(self):
        return self.sdk.current.get("conversation")

    def get_current_turn(self):
        return self.sdk.current.get("turn")

    @staticmethod
    def Message(role, content):  # noqa: N802 - matches weave.conversation.Message
        return {"role": role, "content": content}

    @staticmethod
    def Usage(**counts):  # noqa: N802 - matches weave.conversation.Usage
        return counts


class FakeAgentsWeave(FakeWeave):
    """Weave's Agents surface: a conversation holding turns holding tools and calls."""

    def __init__(self, refuse: bool = False) -> None:
        super().__init__()
        self.refuse = refuse
        self.opened: list[str] = []
        self.closed: list[tuple] = []
        self.current: dict = {}
        self.conversation = FakeConversationModule(self)

    def start_conversation(self, **fields):
        if self.refuse:
            raise RuntimeError("the SDK moved")
        return FakeSpan(self, "conversation", fields)


def _live(monkeypatch, fake):
    monkeypatch.setitem(sys.modules, "weave", fake)
    monkeypatch.setenv("WANDB_PROJECT", "standardphysics")
    assert tracing.init()
    return fake


@pytest.fixture
def agents(monkeypatch):
    return _live(monkeypatch, FakeAgentsWeave())


class TestTheAgentsTab:
    def test_automatic_patching_stays_off(self, weave):
        tracing.init()
        assert weave.settings == [{"implicitly_patch_integrations": False}]

    def test_nothing_is_recorded_without_an_account(self):
        with tracing.start_conversation(agent_name="loop") as conversation:
            with tracing.start_turn(user_message="Pass 1.") as turn:
                with tracing.start_tool(name="assess") as tool:
                    tool.result = "{}"
        spans = (conversation, turn, tool)
        assert all(isinstance(span, tracing.Unrecorded) for span in spans)

    def test_a_pass_nests_under_its_conversation(self, agents):
        with tracing.start_conversation(agent_name="loop"):
            with tracing.start_turn(user_message="Pass 1."):
                with tracing.start_tool(name="assess"):
                    pass
                with tracing.start_llm(model="jev-latest", provider_name="typesafe"):
                    pass
        assert agents.opened == ["conversation", "turn", "tool", "llm"]
        assert [kind for kind, _ in agents.closed] == ["tool", "llm", "turn", "conversation"]

    def test_a_tool_keeps_its_result_and_its_name(self, agents):
        with tracing.start_conversation(agent_name="loop"), tracing.start_turn():
            with tracing.start_tool(name="gate") as tool:
                tool.result = '{"accepted": true}'
        assert (tool.fields, tool.result) == ({"name": "gate"}, '{"accepted": true}')

    def test_a_tool_outside_a_turn_is_not_recorded(self, agents):
        with tracing.start_tool(name="assess") as tool:
            pass
        assert isinstance(tool, tracing.Unrecorded)
        assert agents.opened == []

    def test_an_sdk_that_refuses_costs_the_record_and_nothing_else(self, monkeypatch):
        _live(monkeypatch, FakeAgentsWeave(refuse=True))
        ran = []
        with tracing.start_conversation(agent_name="loop") as conversation:
            ran.append(True)
        assert ran == [True]
        assert isinstance(conversation, tracing.Unrecorded)

    def test_an_error_in_the_work_closes_the_span_and_still_raises(self, agents):
        with pytest.raises(ValueError):
            with tracing.start_conversation(agent_name="loop"):
                raise ValueError("measuring failed")
        assert agents.closed == [("conversation", ValueError)]

    def test_a_model_call_records_one_message_each_way(self, agents):
        with tracing.start_conversation(agent_name="loop"), tracing.start_turn():
            with tracing.start_llm(model="jev-latest", provider_name="typesafe") as llm:
                tracing.record_llm(
                    llm, sent="{}", received="FIX",
                    usage={"input_tokens": 10, "output_tokens": 2, "cost": "unknown"},
                )
        assert llm.recorded == {
            "input_messages": [{"role": "user", "content": "{}"}],
            "output_messages": [{"role": "assistant", "content": "FIX"}],
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }

    def test_recording_on_nothing_is_harmless(self):
        tracing.record_llm(tracing.Unrecorded(), sent="a", received="b")
