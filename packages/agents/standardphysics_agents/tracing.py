"""Weave tracing that survives having no account.

`@traced` goes on every check and every agent call. Once `init()` has run, each
call shows up in the trace tree; until then the decorator costs one attribute
read. Nothing in this lane may require a third-party account in order to run,
because the tests run in CI and CI has no keys.
"""

from __future__ import annotations

import atexit
import functools
import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Callable, TypeVar

Fn = TypeVar("Fn", bound=Callable[..., Any])

PROJECT_ENV = "WANDB_PROJECT"
ENTITY_ENV = "WANDB_ENTITY"
API_KEY_ENV = "WANDB_API_KEY"

log = logging.getLogger(__name__)


class _Tracing:
    """Where `@traced` is writing right now."""

    def __init__(self) -> None:
        self.project: str | None = None
        self.off_because: str | None = "init() has not run"
        self._weave: Any = None
        self._ops: dict[Any, Callable[..., Any]] = {}
        self._flush_registered = False

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
        if not _open_project(module, target):
            self.off_because = f"weave.init failed for {target}"
            return False
        self._weave, self.project, self.off_because = module, target, None
        self._flush_at_exit()
        return True

    def stop(self) -> None:
        if self._weave is not None:
            _flush(self._weave)
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


def _flush(module: Any) -> None:
    """Drain the queue of calls not yet sent. A failure here is logged and
    swallowed, because it happens on the way out and must not stop a shutdown."""
    finish = getattr(module, "finish", None)
    if finish is None:
        return
    try:
        finish()
    except Exception as error:
        log.warning("weave could not flush its last traces: %s", error)


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


def is_live() -> bool:
    return _TRACING.live


def project_url() -> str | None:
    if _TRACING.project is None:
        return None
    return f"https://wandb.ai/{_TRACING.project}/weave"


def tracing_status() -> dict[str, Any]:
    """Whether traces are being sent and where, or why not, for /health/details."""
    return {"active": _TRACING.live, "project_url": project_url(), "off_because": _TRACING.off_because}


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
