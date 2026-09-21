"""The vocabulary the whole app reads a room through.

Importing this package registers every primitive. A rule names one instead of
carrying a hand-written check, and a planner is shown the same list and
composes an answer out of it, so adding a primitive widens both at once.
"""

from . import measurements, scene_reads  # noqa: F401  (imported for the side effect of registering)
from .registry import (
    BadArguments,
    Context,
    Primitive,
    UnknownPrimitive,
    call,
    primitive,
    vocabulary,
)

__all__ = [
    "BadArguments",
    "Context",
    "Primitive",
    "UnknownPrimitive",
    "call",
    "primitive",
    "vocabulary",
]
