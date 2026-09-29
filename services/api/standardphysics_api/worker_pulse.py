"""What each worker loop is doing, kept in memory for /health, and the waits between its retries."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

FIRST_RETRY_SECONDS = 1.0
LONGEST_RETRY_SECONDS = 60.0
STALLED_AFTER_SECONDS = 120.0
"""An idle loop beats at least every IDLE_WAIT_SECONDS and waits at most
LONGEST_RETRY_SECONDS between retries, so an idle loop that has not beaten for
this long is stuck somewhere. A loop running a job is never called stalled,
because a photo bake takes up to fifteen minutes and the loop beats only when
it ends. A job that runs past its kind's deadline is reported overdue instead."""
PROBLEM_STATES = ("stopped", "stalled", "overdue")
"""Loop states that mean jobs are not getting done, worst first."""


class Backoff:
    """Waits that double from FIRST_RETRY_SECONDS up to LONGEST_RETRY_SECONDS."""

    def __init__(self):
        self._next = FIRST_RETRY_SECONDS

    def delay(self) -> float:
        current = min(self._next, LONGEST_RETRY_SECONDS)
        self._next = current * 2
        return current

    def reset(self) -> None:
        self._next = FIRST_RETRY_SECONDS


class JobOverran(RuntimeError):
    """A job ran past its kind's deadline and was stopped at the next stage boundary."""


@dataclass(frozen=True)
class RunningJob:
    kind: str
    id: int
    started_at: float
    deadline_seconds: float

    def running_seconds(self, now: float) -> float:
        return now - self.started_at

    def overdue(self, now: float) -> bool:
        return self.running_seconds(now) > self.deadline_seconds

    def seconds_left(self, now: float) -> float | None:
        """How long the job may still run, or None when its kind has no deadline."""
        if math.isinf(self.deadline_seconds):
            return None
        return max(self.deadline_seconds - self.running_seconds(now), 0.0)

    def overran(self) -> JobOverran:
        limit = describe_duration(self.deadline_seconds)
        return JobOverran(f"The {self.kind} job did not finish within {limit} and was stopped")

    def stop_if_overdue(self) -> None:
        if self.overdue(time.monotonic()):
            raise self.overran()


@dataclass
class LoopPulse:
    """What one worker loop is doing, kept in memory so /health can read it without the database."""

    thread: threading.Thread | None = None
    beat_at: float | None = None
    job: RunningJob | None = None
    """The job running now. One attribute, so a reader never sees half of it."""

    def beat(self) -> None:
        self.beat_at = time.monotonic()

    def begin(self, job: RunningJob) -> None:
        self.job = job

    def end(self) -> None:
        self.job = None
        self.beat()

    def state(self, now: float) -> str:
        if self.thread is None:
            return "not_started"
        if not self.thread.is_alive():
            return "stopped"
        if self.job is not None:
            return "overdue" if self.job.overdue(now) else "busy"
        if self.beat_at is None or now - self.beat_at > STALLED_AFTER_SECONDS:
            return "stalled"
        return "idle"

    def report(self, now: float) -> dict:
        job = self.job
        return {
            "state": self.state(now),
            "heartbeat_seconds": None if self.beat_at is None else round(now - self.beat_at, 1),
            "job": None if job is None else {
                "kind": job.kind,
                "id": job.id,
                "running_seconds": round(job.running_seconds(now), 1),
            },
        }


def describe_duration(seconds: float) -> str:
    if seconds >= 60 and seconds % 60 == 0:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'' if minutes == 1 else 's'}"
    return f"{seconds:g} second{'' if seconds == 1 else 's'}"
