"""The file lock that lets exactly one worker run a database's jobs.

At startup the worker queues again every job left running, which is only safe
when no other process is running them. An exclusive `flock` on a file beside
the database makes that true rather than assumed: the kernel drops the lock
when the holding process exits, however it exits, so a crash never leaves a
stale lock behind for the next start to trip over.
"""

from __future__ import annotations

import fcntl
import os
import pathlib


class WorkerLock:
    def __init__(self, database_path: pathlib.Path):
        self.path = database_path.with_name(database_path.name + ".worker.lock")
        self._descriptor: int | None = None

    @property
    def held(self) -> bool:
        return self._descriptor is not None

    def acquire(self) -> bool:
        """Take the lock without waiting. False means another process holds it."""
        if self.held:
            return True
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return False
        os.ftruncate(descriptor, 0)
        os.write(descriptor, str(os.getpid()).encode())
        self._descriptor = descriptor
        return True

    def holder(self) -> str:
        """The process ID the holder wrote, for the log line that says who has it."""
        try:
            return self.path.read_text().strip() or "unknown"
        except OSError:
            return "unknown"

    def release(self) -> None:
        if self._descriptor is None:
            return
        fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        os.close(self._descriptor)
        self._descriptor = None
