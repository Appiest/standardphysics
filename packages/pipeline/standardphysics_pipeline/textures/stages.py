"""Which step of a texture build is running and how far through it, for the log and for whoever is watching."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

log = logging.getLogger("standardphysics.textures")


@dataclass(frozen=True)
class StepProgress:
    step: str
    done: int | None
    """How many of the step's `total` items are finished, when the step works through a known number."""
    total: int | None
    started_at: float
    """Seconds since the epoch when the step began."""


_steps: list[tuple[str, float]] = []
_listeners: list[Callable[[StepProgress], None]] = []


@contextmanager
def listening(listener: Callable[[StepProgress], None]):
    """Tell `listener` every time a step starts or moves forward, until the block ends."""
    _listeners.append(listener)
    try:
        yield
    finally:
        _listeners.remove(listener)


@contextmanager
def timed(step: str):
    """Run a step, reporting it as the current one and logging how long it took.

    Steps nest: a step inside another is the current one until it ends, and
    then the outer one is current again, with no count of its own.
    """
    started = time.time()
    _steps.append((step, started))
    _tell(StepProgress(step, None, None, started))
    try:
        yield
    finally:
        _steps.pop()
        log.info("texture step %s took %.1f s", step, time.time() - started)
        if _steps:
            outer, outer_started = _steps[-1]
            _tell(StepProgress(outer, None, None, outer_started))


def advanced(done: int, total: int) -> None:
    """Report that `done` of the current step's `total` items are finished."""
    if _steps and _listeners:
        step, started = _steps[-1]
        _tell(StepProgress(step, done, total, started))


def _tell(progress: StepProgress) -> None:
    for listener in _listeners:
        listener(progress)
