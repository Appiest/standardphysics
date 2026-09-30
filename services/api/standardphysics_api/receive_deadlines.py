"""How long a client may take to send a request body.

A body read to the end holds whatever it was admitted with until then: a
connection, an upload reservation, a staged file. A client that sends a few
bytes and goes quiet would hold them for ever, so every body is read against
two deadlines. The idle one ends a body that stops arriving; the total one ends
a body that trickles in a byte at a time and never goes idle.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterable, AsyncIterator, Awaitable
from dataclasses import dataclass
from typing import TypeVar

import anyio

Part = TypeVar("Part")


class BodyTooSlow(ValueError):
    """The client went quiet for too long, or took too long in all, to send its body."""


@dataclass(frozen=True)
class ReceiveDeadlines:
    idle_seconds: float = 120.0
    total_seconds: float = 2 * 60 * 60

    def start(self) -> ReceiveClock:
        """Start the total deadline for one body now."""
        return ReceiveClock(self, time.monotonic() + self.total_seconds)


DEFAULT_DEADLINES = ReceiveDeadlines()


@dataclass(frozen=True)
class ReceiveClock:
    deadlines: ReceiveDeadlines
    finish_by: float

    async def next_part(self, arriving: Awaitable[Part]) -> Part:
        """Wait for the next part of the body, or raise `BodyTooSlow` once either deadline passes."""
        wait = max(min(self.deadlines.idle_seconds, self.finish_by - time.monotonic()), 0.0)
        try:
            with anyio.fail_after(wait):
                return await arriving
        except TimeoutError:
            raise BodyTooSlow(self._why()) from None

    async def paced(self, chunks: AsyncIterable[bytes]) -> AsyncIterator[bytes]:
        """The same chunks, each waited for under the deadlines."""
        iterator = aiter(chunks)
        while True:
            try:
                chunk = await self.next_part(anext(iterator))
            except StopAsyncIteration:
                return
            yield chunk

    def _why(self) -> str:
        if time.monotonic() >= self.finish_by:
            return f"the body took longer than {self.deadlines.total_seconds:g} seconds to arrive"
        return f"no part of the body arrived for {self.deadlines.idle_seconds:g} seconds"
