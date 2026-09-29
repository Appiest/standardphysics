"""Running one function in a spawned child process the worker can kill, and telling which errors pass.

The child gets its arguments by pickling, never a live connection, and leads a
process group of its own, so a child still running at its time limit is killed
with everything it started. A spawned child inherits none of the parent's
tracing, so whatever it runs starts Weave from the settings itself.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import signal
import sqlite3
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass

from standardphysics_agents.tracing import flush_was_abandoned

from .worker_pulse import describe_duration

log = logging.getLogger(__name__)

LONGEST_CHILD_ERROR = 2000
"""How many characters of a child's error cross back to the worker. A pipe holds far more, so
the child's last write never blocks waiting for a parent that is itself waiting for the child to exit."""


class ChildTimedOut(RuntimeError):
    """A child ran past its time limit and was killed."""


class ChildFailed(RuntimeError):
    """A function run in its own process raised. What it raised crosses back as text, with
    whether it goes away by itself, because an exception's cause and context don't pickle."""

    def __init__(self, description: str, transient: bool = False):
        super().__init__(description)
        self.transient = transient


@dataclass(frozen=True)
class _ChildReport:
    value: object = None
    failure: str | None = None
    transient: bool = False


def in_own_process(function: Callable[..., object], *args: object, timeout_seconds: float | None = None) -> object:
    """Run a module-level function in a fresh interpreter, wait, and return what it returned.

    A child still running after `timeout_seconds` is killed along with every
    process it started, such as a Blender run, so one hung stage can't hold its
    loop, and every job queued behind it, for ever. What the function raised is
    raised here as `ChildFailed`; a child that died without a word, as `RuntimeError`.
    """
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    child = context.Process(target=_report_to_parent, args=(sender, function, *args), daemon=True)
    child.start()
    sender.close()
    try:
        child.join(timeout_seconds)
        if child.is_alive():
            _kill_with_everything_it_started(child)
            raise ChildTimedOut(
                f"{function.__name__} did not finish within {describe_duration(timeout_seconds or 0)} and was stopped"
            )
        report = _received(receiver)
    finally:
        receiver.close()
    return _outcome(function, child.exitcode, report)


def _report_to_parent(sender, function: Callable[..., object], *args: object) -> None:
    """The child's side: lead a process group of its own, run the function, and send back how it went."""
    os.setpgrp()
    logging.basicConfig(level=logging.INFO)
    try:
        report = _ChildReport(value=function(*args))
    except Exception as error:
        log.exception("%s failed in its own process", function.__name__)
        report = _ChildReport(
            failure=f"{type(error).__name__}: {error}"[:LONGEST_CHILD_ERROR], transient=is_transient(error)
        )
    sender.send(report)
    sender.close()
    _exit_past_a_stalled_flush()


def _exit_past_a_stalled_flush() -> None:
    """End the child now if Weave could not flush in time.

    The SDK's own exit handlers wait, without a limit, for the queue that just
    failed to drain, so an ordinary exit would sit there until the worker kills
    the child at its job's deadline and fails a job that finished. The report is
    already with the parent, and every database write was committed in its own
    transaction, so skipping those handlers loses only the traces that were lost anyway.
    """
    if flush_was_abandoned():
        logging.shutdown()
        os._exit(0)


def _kill_with_everything_it_started(child) -> None:
    """Kill the child's process group, or only the child when it was killed before it could lead one."""
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        child.kill()
    child.join()


def _received(receiver) -> _ChildReport | None:
    if not receiver.poll():
        return None
    try:
        return receiver.recv()
    except EOFError:
        return None


def _outcome(function: Callable[..., object], exitcode: int | None, report: _ChildReport | None) -> object:
    if report is not None and report.failure is not None:
        raise ChildFailed(report.failure, report.transient)
    if exitcode != 0 or report is None:
        raise RuntimeError(f"{function.__name__} exited with code {exitcode}")
    return report.value


def is_transient(error: BaseException) -> bool:
    """Whether this error, or any error it was raised from, goes away by itself."""
    seen: set[int] = set()
    link: BaseException | None = error
    while link is not None and id(link) not in seen:
        if _transient_alone(link):
            return True
        seen.add(id(link))
        link = link.__cause__ or link.__context__
    return False


def is_lock_contention(error: sqlite3.OperationalError) -> bool:
    """Another writer holds the database past its busy timeout, which ends when that writer commits."""
    return "database is locked" in str(error) or "database is busy" in str(error)


def _transient_alone(error: BaseException) -> bool:
    if isinstance(error, ChildFailed):
        return error.transient
    if isinstance(error, sqlite3.OperationalError):
        return is_lock_contention(error)
    if isinstance(error, urllib.error.URLError):
        return isinstance(error.reason, TimeoutError)
    return isinstance(error, TimeoutError)
