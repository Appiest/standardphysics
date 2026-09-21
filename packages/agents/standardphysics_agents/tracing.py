"""Weave tracing that survives having no account.

`@traced` goes on every check and every agent call. Once `init()` has run, each
call shows up in the trace tree; until then the decorator costs one attribute
read. Nothing in this lane may require a third-party account in order to run,
because the tests run in CI and CI has no keys.
"""

from __future__ import annotations

import functools
import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Callable, TypeVar

Fn = TypeVar("Fn", bound=Callable[..., Any])

PROJECT_ENV = "WANDB_PROJECT"
ENTITY_ENV = "WANDB_ENTITY"

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
        module.init(target)
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
