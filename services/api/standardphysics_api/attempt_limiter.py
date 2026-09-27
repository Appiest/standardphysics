"""How often one key may try something, within this process.

One API process owns one database (see `worker`), so a per-process counter
covers the whole deployment. It is memory only: a restart forgives.

Handlers run on a thread pool, so checking a key and recording the attempt
happen under one lock. Checking first and recording after the password was
verified let a burst of parallel guesses all pass the check before any was
counted.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

from .errors import ApiProblem

MAX_KEYS = 50_000
"""Enough for every address and email that tries within one window on a busy day.
At a few hundred bytes a key, the limiter never holds more than a few tens of MB."""


@dataclass
class AttemptLimiter:
    limit: int
    window: float
    message: str = "Too many attempts. Wait a few minutes and try again."
    max_keys: int = MAX_KEYS
    clock: Callable[[], float] = time.monotonic
    _attempts: OrderedDict[str, deque[float]] = field(default_factory=OrderedDict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _swept_at: float = field(default=float("-inf"), init=False)

    def admit(self, *keys: str) -> None:
        """Count one attempt against every key, or raise 429 and count nothing when any key is spent."""
        with self._lock:
            now = self.clock()
            self._sweep(now)
            if any(len(self._recent(key, now)) >= self.limit for key in keys):
                raise ApiProblem(429, self.message)
            for key in keys:
                self._record(key, now)

    def refund(self, key: str) -> None:
        """Take back the most recent attempt, for one that turned out not to be a guess."""
        with self._lock:
            recent = self._attempts.get(key)
            if recent:
                recent.pop()

    def forget(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)

    def __len__(self) -> int:
        return len(self._attempts)

    def _recent(self, key: str, now: float) -> deque[float]:
        recent = self._attempts.get(key, deque())
        while recent and now - recent[0] >= self.window:
            recent.popleft()
        return recent

    def _record(self, key: str, now: float) -> None:
        recent = self._recent(key, now)
        recent.append(now)
        self._attempts[key] = recent
        self._attempts.move_to_end(key)
        while len(self._attempts) > self.max_keys:
            self._attempts.popitem(last=False)

    def _sweep(self, now: float) -> None:
        """Drop keys whose newest attempt is a window old, at most once a window.

        Keys are kept in order of their latest attempt, so the stale ones are
        all at the front and the sweep stops at the first live key.
        """
        if now - self._swept_at < self.window:
            return
        self._swept_at = now
        while self._attempts:
            oldest_key, attempts = next(iter(self._attempts.items()))
            if attempts and now - attempts[-1] < self.window:
                return
            del self._attempts[oldest_key]
