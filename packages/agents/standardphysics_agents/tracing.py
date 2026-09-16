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
"""

from __future__ import annotations

import functools
import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

Fn = TypeVar("Fn", bound=Callable[..., Any])

PROJECT_ENV = "WANDB_PROJECT"
ENTITY_ENV = "WANDB_ENTITY"

WEAVE_SETTINGS = {"implicitly_patch_integrations": False}
"""Every agent and model span is opened by hand, so Weave's automatic patching
of model clients stays off. With both on, one call is recorded twice."""

USAGE_FIELDS = ("input_tokens", "output_tokens")

log = logging.getLogger(__name__)


class _Tracing:
    """Where `@traced` is writing right now."""

    def __init__(self) -> None:
        self.project: str | None = None
        self._weave: Any = None
        self._ops: dict[Any, Callable[..., Any]] = {}

    @property
    def live(self) -> bool:
        return self._weave is not None

    def start(self, project: str | None, entity: str | None) -> bool:
        target = _project_name(project, entity)
        if target is None:
            return False
        module = _import_weave()
        if module is None:
            return False
        if not _open_project(module, target):
            return False
        self._weave, self.project = module, target
        return True

    def stop(self) -> None:
        self._weave, self.project, self._ops = None, None, {}

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


def _import_weave() -> Any:
    try:
        import weave
    except ImportError:
        return None
    return weave


def _open_project(module: Any, target: str) -> bool:
    """`weave.init` needs a key and a network, so it fails for reasons the
    caller cannot see coming: a rejected key, no connection, a project the
    account cannot write to. Any of those leaves tracing off and the server
    running, because this lane may not require a third-party account.
    """
    try:
        module.init(target, settings=dict(WEAVE_SETTINGS))
    except Exception as error:
        log.warning("weave tracing is off, %s said: %s", target, error)
        return False
    log.info("weave tracing is on for %s", target)
    return True


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
    _TRACING.stop()


def is_live() -> bool:
    return _TRACING.live


def project_url() -> str | None:
    if _TRACING.project is None:
        return None
    return f"https://wandb.ai/{_TRACING.project}/weave"


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
