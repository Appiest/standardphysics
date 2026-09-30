"""A size cap on every request body except the uploads that stream to disk.

The proxy in front lets a request carry a gigabyte, because a walk's mesh and
photos are that large. A JSON route reads its whole body into memory before a
single field is checked, so without a cap of its own a sign-in could be made
to hold a gigabyte. The cap is applied before the route runs: a declared
Content-Length over it is refused unread, and a body with no length, sent in
chunks, is read here up to the cap and refused the moment it passes it. A body
under the cap is handed to the route exactly as it arrived. It is read under
the same `ReceiveDeadlines` as a streamed upload, so a client that stops
sending partway is answered with a 408 instead of holding the request open.
"""

from __future__ import annotations

import collections
import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import ApiProblem
from .receive_deadlines import DEFAULT_DEADLINES, BodyTooSlow, ReceiveClock, ReceiveDeadlines

STREAMED_UPLOADS = re.compile(r"^/api/scans/[^/]+/(artifacts/[^/]+|requests/[^/]+/photo)$")
"""The routes that stream their body to disk under a cap of their own: artifacts (see `store`) and
answer photos (see `owner_routes`). Both are PUTs."""


def _streams_to_disk(scope: Scope) -> bool:
    return scope["method"] == "PUT" and bool(STREAMED_UPLOADS.match(scope["path"]))


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length" and value.isdigit():
            return int(value)
    return None


def _problem(problem: ApiProblem) -> JSONResponse:
    return JSONResponse(problem.body.model_dump(exclude_none=True), status_code=problem.status)


def _too_large(limit: int) -> JSONResponse:
    return _problem(ApiProblem(413, f"request body too large: this route takes at most {limit} bytes"))


def _too_slow(slow: BodyTooSlow) -> JSONResponse:
    return _problem(ApiProblem(408, f"The request stopped arriving: {slow}. Send it again."))


async def _read_within(receive: Receive, limit: int, clock: ReceiveClock) -> list[Message] | None:
    """Every message of the body, or None as soon as the body passes `limit` bytes."""
    messages: list[Message] = []
    received = 0
    while True:
        message = await clock.next_part(receive())
        messages.append(message)
        if message["type"] != "http.request":
            return messages
        received += len(message.get("body", b""))
        if received > limit:
            return None
        if not message.get("more_body", False):
            return messages


def _replaying(messages: list[Message], receive: Receive) -> Receive:
    """A receive that hands back the body already read, then listens to the client as before."""
    pending = collections.deque(messages)

    async def replay() -> Message:
        return pending.popleft() if pending else await receive()

    return replay


class BoundedRequestBodies:
    """ASGI middleware refusing any body over `max_bytes` with a 413, and any too slow to arrive with a 408,
    outside the streamed uploads, which the store holds to the same deadlines."""

    def __init__(self, app: ASGIApp, max_bytes: int, deadlines: ReceiveDeadlines = DEFAULT_DEADLINES):
        self.app, self.max_bytes, self.deadlines = app, max_bytes, deadlines

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _streams_to_disk(scope):
            await self.app(scope, receive, send)
            return
        declared = _declared_length(scope)
        if declared is not None and declared > self.max_bytes:
            await _too_large(self.max_bytes)(scope, receive, send)
            return
        try:
            messages = await _read_within(receive, self.max_bytes, self.deadlines.start())
        except BodyTooSlow as slow:
            await _too_slow(slow)(scope, receive, send)
            return
        if messages is None:
            await _too_large(self.max_bytes)(scope, receive, send)
            return
        await self.app(scope, _replaying(messages, receive), send)
