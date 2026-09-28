"""Weave tracing that survives having no account.

`@traced` goes on every check and every agent call. Once `init()` has run, each
call shows up in the trace tree; until then the decorator costs one attribute
read. Nothing in this lane may require a third-party account in order to run,
because the tests run in CI and CI has no keys.

The loop also reports itself to Weave's Agents tab: one conversation per run,
one turn per pass, a tool span for each thing a pass does and a chat span for
each model call. `start_conversation`, `start_turn`, `start_tool` and
`start_llm` wrap the SDK calls of the same names. With tracing off, or no
conversation open, each yields an `Unrecorded` that accepts the same writes and
keeps none of them.

Nothing here may hold up the process either. `weave.init` and the final flush
both talk to W&B over the network, so each runs on a daemon thread and is given
up on after its deadline: API startup and a job child's exit wait that long at
most for an endpoint that has stopped answering.
"""

from __future__ import annotations

import atexit
import functools
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

Fn = TypeVar("Fn", bound=Callable[..., Any])

PROJECT_ENV = "WANDB_PROJECT"
ENTITY_ENV = "WANDB_ENTITY"
API_KEY_ENV = "WANDB_API_KEY"
INIT_DEADLINE_ENV = "SP_WEAVE_INIT_TIMEOUT_SECONDS"
FLUSH_DEADLINE_ENV = "SP_WEAVE_FLUSH_TIMEOUT_SECONDS"
INIT_DEADLINE_SECONDS = 30.0
"""How long `weave.init` may take before tracing is left off. It checks the key and
creates the project, a few round trips that take a second or two when W&B is answering."""
FLUSH_DEADLINE_SECONDS = 15.0
"""How long draining the queue of unsent calls may take on the way out."""
SENDER_LOGGER = "weave.trace_server_bindings"
"""Where the Weave SDK logs a batch of calls it could not send."""

WEAVE_SETTINGS = {"implicitly_patch_integrations": False}
"""Every agent and model span is opened by hand, so Weave's automatic patching
of model clients stays off. With both on, one call is recorded twice."""

USAGE_FIELDS = ("input_tokens", "output_tokens")

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Outcome:
    finished: bool
    error: Exception | None = None


@dataclass(frozen=True)
class _FlushFailure:
    message: str
    abandoned: bool


def _run_with_deadline(work: Callable[[], object], seconds: float, name: str) -> _Outcome:
    """Run `work` on a daemon thread and stop waiting for it after `seconds`.

    A thread can't be stopped from outside, so one that is still waiting on the
    network is abandoned rather than killed. It is a daemon, so it never keeps
    the process alive.
    """
    errors: list[Exception] = []

    def run() -> None:
        try:
            work()
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=run, name=name, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        return _Outcome(finished=False)
    return _Outcome(finished=True, error=errors[0] if errors else None)


def _deadline(env: str, default: float) -> float:
    raw = os.environ.get(env)
    if not raw:
        return default
    try:
        seconds = float(raw)
    except ValueError:
        seconds = -1.0
    if seconds > 0:
        return seconds
    log.warning("%s=%s is not a positive number of seconds, so %s is used", env, raw, default)
    return default


class _SenderErrors(logging.Handler):
    """Counts what the Weave SDK logs at ERROR while sending calls.

    The SDK sends from a background queue and never tells its caller whether a
    call arrived; when a batch is dropped it logs the fact and moves on. These
    log records are the only failures it surfaces, so a count of zero means
    none were logged, not that every call was delivered.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.count = 0
        self.last: str | None = None

    def emit(self, record: logging.LogRecord) -> None:
        self.count += 1
        self.last = record.getMessage()[:500]

    def note(self, message: str) -> None:
        self.count += 1
        self.last = message


class _Tracing:
    """Where `@traced` is writing right now."""

    def __init__(self) -> None:
        self.project: str | None = None
        self.off_because: str | None = "init() has not run"
        self._weave: Any = None
        self._ops: dict[Any, Callable[..., Any]] = {}
        self._flush_registered = False
        self.sender_errors = _SenderErrors()
        self.flush_abandoned = False

    @property
    def live(self) -> bool:
        return self._weave is not None

    def start(self, project: str | None, entity: str | None) -> bool:
        target = _project_name(project, entity)
        if target is None:
            _warn_if_key_has_no_project()
            self.off_because = f"{PROJECT_ENV} is not set"
            return False
        module = _import_weave(target)
        if module is None:
            self.off_because = "the weave SDK could not be imported"
            return False
        refusal = _open_project(module, target)
        if refusal is not None:
            self.off_because = refusal
            return False
        self._weave, self.project, self.off_because = module, target, None
        logging.getLogger(SENDER_LOGGER).addHandler(self.sender_errors)
        self._flush_at_exit()
        return True

    def stop(self) -> None:
        failure = _flush(self._weave) if self._weave is not None else None
        if failure is not None:
            self.flush_abandoned = self.flush_abandoned or failure.abandoned
            self.sender_errors.note(failure.message)
        logging.getLogger(SENDER_LOGGER).removeHandler(self.sender_errors)
        self._weave, self.project, self._ops = None, None, {}
        self.off_because = "tracing was shut down"

    def _flush_at_exit(self) -> None:
        """Weave sends calls from a background queue. A process that exits
        without draining it loses the last calls it made, which are the ones
        that explain why it exited. The API's shutdown flushes first; this
        covers scripts and a process that never reaches its shutdown."""
        if not self._flush_registered:
            atexit.register(self.stop)
            self._flush_registered = True

    def op(self, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        if fn not in self._ops:
            self._ops[fn] = self._build(name, fn)
        return self._ops[fn]

    def _build(self, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap once, whichever way this version of Weave spells it.

        `weave.op` has been both a decorator factory and a plain decorator. A
        traced call is on every check and every agent call, so the one thing it
        may not do is raise because the SDK moved.
        """
        for attempt in (
            lambda: self._weave.op(name=name)(fn),
            lambda: self._weave.op(fn, name=name),
            lambda: self._weave.op(fn),
        ):
            try:
                return attempt()
            except TypeError:
                continue
        return fn

    def open_span(self, opener: Callable[[Any], Any]) -> Any:
        """Enter the span `opener` builds, or return None.

        A span is a record of work that is going to happen anyway, so an SDK
        that refuses to open one costs the record and nothing else.
        """
        if not self.live:
            return None
        try:
            span = opener(self._weave)
            return None if span is None else span.__enter__()
        except Exception as error:
            log.warning("weave span skipped: %s", error)
            return None

    def close_span(self, span: Any, error: BaseException | None) -> None:
        kind = type(error) if error is not None else None
        traceback = error.__traceback__ if error is not None else None
        try:
            span.__exit__(kind, error, traceback)
        except Exception as failure:
            log.warning("weave span did not close: %s", failure)

    def message_types(self) -> Any:
        return self._weave.conversation


class Unrecorded:
    """Where a span's fields go when nothing is recording them."""

    result: Any = None

    def record(self, **_fields: Any) -> None:
        return None


_TRACING = _Tracing()
_THREAD_STATE = threading.local()


@contextmanager
def suspend_tracing():
    """Skip per-operation telemetry inside high-concurrency simulation lanes.

    The simulation result remains identical; this only prevents hundreds of
    provider workers from serializing on Weave's local trace store.
    """
    previous = getattr(_THREAD_STATE, "suspended", False)
    _THREAD_STATE.suspended = True
    try:
        yield
    finally:
        _THREAD_STATE.suspended = previous


def _import_weave(target: str) -> Any:
    """A project was asked for, so a missing or broken SDK is a deployment
    mistake worth a warning: without one, production runs untraced and nothing
    says why. It still leaves the server running.
    """
    try:
        import weave
    except ImportError:
        log.warning(
            "weave tracing is off for %s: the weave SDK is not installed. "
            "Install standardphysics-agents[observability].",
            target,
        )
        return None
    except Exception as error:
        log.warning("weave tracing is off for %s: importing weave failed: %s", target, error)
        return None
    return weave


def _warn_if_key_has_no_project() -> None:
    if os.environ.get(API_KEY_ENV):
        log.warning("weave tracing is off: %s is set but %s is not.", API_KEY_ENV, PROJECT_ENV)


def _open_project(module: Any, target: str) -> str | None:
    """Why `weave.init` left tracing off, or None when it came up.

    `weave.init` needs a key and a network, so it fails for reasons the caller
    cannot see coming: a rejected key, no connection, a project the account
    cannot write to, an endpoint that accepts the connection and never answers.
    Any of those leaves tracing off and the server running, because this lane
    may not require a third-party account.
    """
    seconds = _deadline(INIT_DEADLINE_ENV, INIT_DEADLINE_SECONDS)
    outcome = _run_with_deadline(lambda: module.init(target, settings=dict(WEAVE_SETTINGS)), seconds, "weave-init")
    if not outcome.finished:
        log.warning("weave tracing is off: weave.init for %s did not finish within %s s", target, seconds)
        return f"weave.init for {target} did not finish within {seconds} s"
    if outcome.error is not None:
        log.warning("weave tracing is off, %s said: %s", target, outcome.error)
        return f"weave.init failed for {target}"
    log.info("weave tracing is on for %s", target)
    return None


def _flush(module: Any) -> _FlushFailure | None:
    """Drain the queue of calls not yet sent. Returns what went wrong, if anything.

    A failure here is logged, counted by the caller and otherwise swallowed,
    because it happens on the way out and must not stop a shutdown.
    """
    finish = getattr(module, "finish", None)
    if finish is None:
        return None
    seconds = _deadline(FLUSH_DEADLINE_ENV, FLUSH_DEADLINE_SECONDS)
    outcome = _run_with_deadline(finish, seconds, "weave-flush")
    if not outcome.finished:
        log.warning("weave did not flush its last traces within %s s; the unsent ones are lost", seconds)
        return _FlushFailure(f"weave did not flush within {seconds} s", abandoned=True)
    if outcome.error is not None:
        log.warning("weave could not flush its last traces: %s", outcome.error)
        return _FlushFailure(f"weave could not flush: {outcome.error}"[:500], abandoned=False)
    return None


def _project_name(project: str | None, entity: str | None) -> str | None:
    name = project or os.environ.get(PROJECT_ENV)
    if not name:
        return None
    team = entity or os.environ.get(ENTITY_ENV)
    return f"{team}/{name}" if team and "/" not in name else name


def init(project: str | None = None, entity: str | None = None) -> bool:
    """Turn tracing on. Returns whether it actually came up.

    Called once at API startup. A missing project or a missing Weave install
    leaves tracing off and every traced function calls straight through.
    """
    return _TRACING.start(project, entity)


def shutdown() -> None:
    """Send every trace still queued, then stop tracing. Called on API shutdown."""
    _TRACING.stop()


@contextmanager
def tracing_for_this_process(project: str | None = None, entity: str | None = None) -> Iterator[bool]:
    """Trace a spawned process's work, and send what it traced before it ends.

    A spawned interpreter starts with none of its parent's state, tracing
    included, so a worker's job child comes up untraced unless it calls
    `init` itself. The block yields whether tracing came up. With no project
    set it costs what `init` costs then: no import and no network.
    """
    started = init(project, entity)
    try:
        yield started
    finally:
        if started:
            shutdown()


def is_live() -> bool:
    return _TRACING.live


def project_url() -> str | None:
    if _TRACING.project is None:
        return None
    return f"https://wandb.ai/{_TRACING.project}/weave"


def flush_was_abandoned() -> bool:
    """Whether a flush in this process ran past its deadline.

    The Weave SDK registers exit handlers that wait, without a limit, for the
    same queue to drain, so a process that saw this should end with `os._exit`
    once its own work is done.
    """
    return _TRACING.flush_abandoned


def tracing_status() -> dict[str, Any]:
    """Whether traces are being sent and where, or why not, for /health/details.

    `delivery_errors` counts the send failures the Weave SDK logged and any
    flush that raised or ran out of time. The SDK reports nothing for a call that
    arrived, so "active" means tracing came up, not that W&B has every call.
    """
    return {
        "active": _TRACING.live,
        "project_url": project_url(),
        "off_because": _TRACING.off_because,
        "delivery_errors": _TRACING.sender_errors.count,
        "last_delivery_error": _TRACING.sender_errors.last,
    }


def traced(name: str) -> Callable[[Fn], Fn]:
    """Name this call in the trace tree."""

    def decorate(fn: Fn) -> Fn:
        @functools.wraps(fn)
        def call(*args: Any, **kwargs: Any) -> Any:
            if not _TRACING.live or getattr(_THREAD_STATE, "suspended", False):
                return fn(*args, **kwargs)
            return _TRACING.op(name, fn)(*args, **kwargs)

        call.traced_name = name  # type: ignore[attr-defined]
        return call  # type: ignore[return-value]

    return decorate


@contextmanager
def _span(opener: Callable[[Any], Any]) -> Iterator[Any]:
    span = _TRACING.open_span(opener)
    if span is None:
        yield Unrecorded()
        return
    try:
        yield span
    except BaseException as error:
        _TRACING.close_span(span, error)
        raise
    _TRACING.close_span(span, None)


def _started(parent: Any, method: str, fields: dict[str, Any]) -> Any:
    return None if parent is None else getattr(parent, method)(**fields)


def start_conversation(**fields: Any):
    """`weave.start_conversation`, as a context manager."""
    return _span(lambda weave: weave.start_conversation(**fields))


def start_turn(**fields: Any):
    """`Conversation.start_turn` on the conversation that is open, if one is."""
    return _span(
        lambda weave: _started(
            weave.conversation.get_current_conversation(), "start_turn", fields
        )
    )


def start_tool(**fields: Any):
    """`Turn.start_tool` on the turn that is open, if one is."""
    return _span(
        lambda weave: _started(weave.conversation.get_current_turn(), "start_tool", fields)
    )


def start_llm(**fields: Any):
    """`Turn.start_llm` on the turn that is open, if one is. Pass `provider_name`."""
    return _span(
        lambda weave: _started(weave.conversation.get_current_turn(), "start_llm", fields)
    )


def record_llm(
    llm: Any, *, sent: str, received: str, usage: dict | None = None
) -> None:
    """`LLM.record` with one message each way and the token counts, if any."""
    if isinstance(llm, Unrecorded):
        return
    try:
        types = _TRACING.message_types()
        llm.record(
            input_messages=[types.Message(role="user", content=sent)],
            output_messages=[types.Message(role="assistant", content=received)],
            usage=_usage(types, usage),
        )
    except Exception as error:
        log.warning("weave did not record the model call: %s", error)


def _usage(types: Any, usage: dict | None) -> Any:
    if not isinstance(usage, dict):
        return None
    counts = {name: usage[name] for name in USAGE_FIELDS if isinstance(usage.get(name), int)}
    return types.Usage(**counts) if counts else None
