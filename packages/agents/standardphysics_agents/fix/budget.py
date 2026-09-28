"""Wall-clock deadlines for searches a person is waiting on.

A deadline is a `time.monotonic()` time. None means no deadline, which is what
training and evaluation use, so their searches run to the end exactly as before.
"""

from __future__ import annotations

from time import monotonic


def deadline_in(seconds: float | None) -> float | None:
    """The deadline `seconds` from now, or None for no deadline."""
    return None if seconds is None else monotonic() + seconds


def out_of_time(deadline: float | None) -> bool:
    """Whether the deadline has passed; a deadline of None never does."""
    return deadline is not None and monotonic() >= deadline
