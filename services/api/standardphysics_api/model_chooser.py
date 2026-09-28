"""A language model picks the layout from the menu of legal moves, for trying the rearranger in the web app.

Set SP_MENU_MODEL_URL to an OpenAI-compatible server (the local MLX server,
for example http://100.120.71.28:8090/v1) and SP_MENU_MODEL to the name it
serves. With both set, "See a layout that fixes this" builds the menu for the
finding the owner asked about, asks the model to choose, and returns its pick
with the model's reason. Without them, proposals come from the search as before.

This is a preview. The menu is built with the training checker, which treats
scan geometry marked "needs another look" as measured, so a pick can rest on a
measurement the production checks would still ask the owner to confirm.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace

from standardphysics_agents.fix import FixOutcome
from standardphysics_agents.fix.search import _build_proposal
from standardphysics_agents.fix.strategies import Candidate
from standardphysics_agents.training.edits import apply_edits, node_moves, parse_edits
from standardphysics_agents.training.menu import Menu, resolve
from standardphysics_contracts import Finding, SceneGraph

REPLY_SECONDS = 600
MAX_REPLY_TOKENS = 256
MENU_SECONDS = 15.0
"""How long the menu may spend measuring options before the model is asked to choose from what it has."""
SEARCH_AFTER_MENU_SECONDS = 10.0
"""How long the search may run when the model's menu had nothing to offer."""
PROVIDER_KEYS = {"api.fireworks.ai": "FIREWORKS_API_KEY", "openrouter.ai": "OPENROUTER_API_KEY"}
"""The environment variable holding each hosted provider's key, used when `<prefix>MODEL_KEY` is unset."""
REASONING_OFF = {"api.fireworks.ai": {"reasoning_effort": "none"}}
"""Hosts that accept turning reasoning off. A menu pick is a short JSON answer, and reasoning tokens would
eat the reply budget and add seconds per turn."""


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).hostname or ""


def _key_for(prefix: str, url: str) -> str:
    return os.environ.get(f"{prefix}MODEL_KEY") or os.environ.get(PROVIDER_KEYS.get(_host(url), ""), "")


@dataclass(frozen=True)
class ModelChooser:
    url: str
    model: str
    label: str = "The model"
    api_key: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls, prefix: str = "SP_MENU_") -> ModelChooser | None:
        """The model named by `<prefix>MODEL_URL` and `<prefix>MODEL`, called `<prefix>MODEL_LABEL` to the owner.
        A hosted provider's key comes from `<prefix>MODEL_KEY`, or else from that provider's usual variable."""
        url, model = os.environ.get(f"{prefix}MODEL_URL"), os.environ.get(f"{prefix}MODEL")
        label = os.environ.get(f"{prefix}MODEL_LABEL", "The model")
        if not url or not model:
            return None
        return cls(url.rstrip("/"), model, label, _key_for(prefix, url))

    def ask(self, messages: list[dict]) -> str:
        body = json.dumps({"model": self.model, "messages": messages, "temperature": 0.0,
                           "max_tokens": MAX_REPLY_TOKENS, **REASONING_OFF.get(_host(self.url), {})}).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(f"{self.url}/chat/completions", data=body, method="POST", headers=headers)
        with urllib.request.urlopen(request, timeout=REPLY_SECONDS) as response:
            reply = json.loads(response.read())
        return reply["choices"][0]["message"]["content"] or ""


def without_wall_shifts(menu: Menu) -> Menu:
    """The menu with furniture and built-in moves only: the owner's plan can show a moved counter, not a moved wall."""
    return replace(menu, options=[option for option in menu.options if not option.edits.wall_shifts])


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
