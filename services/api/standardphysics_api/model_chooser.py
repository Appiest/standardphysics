"""A language model picks the layout from the menu of legal moves, for trying the rearranger in the web app.

Set SP_MENU_MODEL_URL to an OpenAI-compatible server (the local MLX server,
for example http://100.120.71.28:8090/v1) and SP_MENU_MODEL to the name it
serves. With both set, "See a layout that fixes this" builds the menu for the
finding the owner asked about, asks the model to choose, and returns its pick
with the model's reason. Without them, proposals come from the search as before.

This is a preview. The menu is built with the training checker, which treats
scan geometry marked "needs another look" as measured, so a pick can rest on a
measurement the production checks would still ask the owner to confirm.

The server is someone else's, so nothing it sends is trusted: a reply must
arrive within `reply_seconds`, fit in MAX_REPLY_BYTES, and be a chat
completion with a message in it, or `ask` raises `ModelReplyError` (or
`TimeoutError` for a slow one) instead of handing on something it can't read.
`ModelSlots` caps how many model calls run at once, per owner and in total.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field, replace

from pydantic import BaseModel, Field, ValidationError
from standardphysics_agents.fix import FixOutcome
from standardphysics_agents.fix.search import _build_proposal
from standardphysics_agents.fix.strategies import Candidate
from standardphysics_agents.training.edits import apply_edits, node_moves, parse_edits
from standardphysics_agents.training.menu import Menu, resolve
from standardphysics_contracts import Finding, SceneGraph

from .errors import ApiProblem

REPLY_SECONDS = 120.0
"""How long one call may take, unless `<prefix>MODEL_REPLY_SECONDS` says otherwise. The reply is at most
MAX_REPLY_TOKENS, so the time goes on reading the menu, which a local server does in well under a minute."""
MAX_REPLY_TOKENS = 256
MENU_SECONDS = 15.0
"""How long the menu may spend measuring options before the model is asked to choose from what it has."""
SEARCH_AFTER_MENU_SECONDS = 10.0
"""How long the search may run when the model's menu had nothing to offer."""
MAX_REPLY_BYTES = 64 * 1024
"""A chat completion of MAX_REPLY_TOKENS is a few KB, even with the usage block and logprobs some servers add."""
READ_CHUNK_BYTES = 8 * 1024
MODEL_RETRY_SECONDS = 30


class ModelReplyError(Exception):
    """The model server answered with something that isn't a usable chat completion."""


class _Message(BaseModel):
    content: str | None = None


class _Choice(BaseModel):
    message: _Message


class _ChatCompletion(BaseModel):
    choices: list[_Choice] = Field(min_length=1)


def _declared_too_large(response) -> bool:
    declared = response.headers.get("Content-Length")
    return declared is not None and declared.isdigit() and int(declared) > MAX_REPLY_BYTES


def _read_capped(response, deadline: float) -> bytes:
    """The body, refused once it passes MAX_REPLY_BYTES or the deadline, whichever comes first."""
    if _declared_too_large(response):
        raise ModelReplyError(f"sent an answer over {MAX_REPLY_BYTES // 1024} KB")
    body = bytearray()
    while chunk := response.read(READ_CHUNK_BYTES):
        body += chunk
        if len(body) > MAX_REPLY_BYTES:
            raise ModelReplyError(f"sent an answer over {MAX_REPLY_BYTES // 1024} KB")
        if time.monotonic() > deadline:
            raise TimeoutError("the reply was still arriving at the deadline")
    return bytes(body)


def reply_content(body: bytes) -> str:
    """The first choice's message, or ModelReplyError when the body isn't a chat completion."""
    try:
        completion = _ChatCompletion.model_validate_json(body)
    except ValidationError as error:
        raise ModelReplyError("sent an answer that couldn't be read") from error
    return completion.choices[0].message.content or ""


def _reply_seconds(prefix: str) -> float:
    raw = os.environ.get(f"{prefix}MODEL_REPLY_SECONDS")
    seconds = REPLY_SECONDS if raw is None else float(raw)
    if not 0 < seconds <= 600:
        raise ValueError(f"{prefix}MODEL_REPLY_SECONDS must be above 0 and at most 600")
    return seconds


@dataclass(frozen=True)
class ModelChooser:
    url: str
    model: str
    label: str = "The model"
    reply_seconds: float = REPLY_SECONDS

    @classmethod
    def from_environment(cls, prefix: str = "SP_MENU_") -> ModelChooser | None:
        """The model named by `<prefix>MODEL_URL` and `<prefix>MODEL`, called `<prefix>MODEL_LABEL` to the owner."""
        url, model = os.environ.get(f"{prefix}MODEL_URL"), os.environ.get(f"{prefix}MODEL")
        label = os.environ.get(f"{prefix}MODEL_LABEL", "The model")
        return cls(url.rstrip("/"), model, label, _reply_seconds(prefix)) if url and model else None

    def ask(self, messages: list[dict], seconds: float | None = None) -> str:
        """The model's reply within `seconds` (at most `reply_seconds`), or TimeoutError or ModelReplyError."""
        limit = min(seconds or self.reply_seconds, self.reply_seconds)
        body = json.dumps({"model": self.model, "messages": messages, "temperature": 0.0,
                           "max_tokens": MAX_REPLY_TOKENS}).encode()
        request = urllib.request.Request(f"{self.url}/chat/completions", data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=limit) as response:
                return reply_content(_read_capped(response, time.monotonic() + limit))
        except urllib.error.URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise TimeoutError(f"no answer within {limit:g} seconds") from error
            raise


@dataclass
class ModelSlots:
    """Every model call running in this process, capped per owner and in total.

    A preview holds a slot for its one call, and a loop for all its turns. The
    menu is built under the search lock, so previews beyond the cap would only
    queue there, each holding a request thread.
    """

    per_owner: int
    total: int
    _held: Counter[uuid.UUID] = field(default_factory=Counter)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def take(self, owner_id: uuid.UUID) -> None:
        """Claim a slot for this owner, or raise 429 when they hold their share and 503 when every slot is taken."""
        retry = {"Retry-After": str(MODEL_RETRY_SECONDS)}
        with self._lock:
            if self._held[owner_id] >= self.per_owner:
                raise ApiProblem(429, "The model is already working on this account's layouts. "
                                 "Try again when it finishes.", headers=retry)
            if self._held.total() >= self.total:
                raise ApiProblem(503, "The model is busy with other layouts right now. Try again in a minute.",
                                 headers=retry)
            self._held[owner_id] += 1

    def give_back(self, owner_id: uuid.UUID) -> None:
        with self._lock:
            self._held[owner_id] -= 1
            if self._held[owner_id] <= 0:
                del self._held[owner_id]

    @contextlib.contextmanager
    def held(self, owner_id: uuid.UUID) -> Iterator[None]:
        self.take(owner_id)
        try:
            yield
        finally:
            self.give_back(owner_id)


def furniture_only(menu: Menu) -> Menu:
    """The menu without construction options, since a proposal or a plan here carries furniture moves only."""
    return replace(menu, options=[option for option in menu.options
                                  if not option.edits.fixture_moves and not option.edits.wall_shifts])


def menu_for_findings(menu: Menu, targets: list[Finding]) -> Menu | None:
    """The menu cut to furniture options that clear or improve a finding the owner asked about; None when empty."""
    wanted = {menu.problems[finding.id] for finding in targets if finding.id in menu.problems}
    options = [option for option in furniture_only(menu).options if (
        wanted & set(option.effect.get("clears", []))
        or wanted & {item["problem"] for item in option.effect.get("improves", [])})]
    return replace(menu, options=options) if options else None


def picked_outcome(graph: SceneGraph, checker, menu: Menu, reply: str, targets: list[Finding]) -> FixOutcome | None:
    """The model's reply as a proposal, or None when it picked nothing usable."""
    resolution = resolve(reply, graph, menu, checker.pinned)
    edits = parse_edits(resolution.completion)
    moves = node_moves(edits) if edits else []
    if edits is None or not moves:
        return None
    after = apply_edits(graph, edits)
    proposal = _build_proposal(graph, after, Candidate("model_choice", moves, 0.0), tuple(f.id for f in targets))
    wordings = [menu.picked_in_owner_words(number) for number in resolution.applied]
    picked = "; ".join(wordings) or "its own moves, snapped to legal floor"
    reason = f" Its reason: {menu.in_owner_words(resolution.why)}" if resolution.why else ""
    return FixOutcome(proposal=proposal, graph=after, message=f"The model picked: {picked}.{reason}",
                      targets=tuple(f.id for f in targets))
