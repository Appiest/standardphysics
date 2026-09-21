"""TypeSafe picks the next action, and its answer drives real control flow.

TypeSafe's live API evaluates typed questions. It does not accept an arbitrary
JSON schema, so the router asks one closed-set Choice question for the next
action and assembles eligible finding targets in code. The answer still passes
through ``parse_decision`` and the authorization checks before it can reach a
branch.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from standardphysics_contracts import Decision

from ..tracing import traced
from .decision import ACTIONS, Rejected, parse_decision
from .state import RouterState

API_KEY_ENV = "TYPESAFE_API_KEY"
BASE_URL_ENV = "TYPESAFE_BASE_URL"
MODEL_ENV = "TYPESAFE_MODEL"

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_PATH = "/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_TIMEOUT_SECONDS = 20.0

PROVIDER = "typesafe"

INSTRUCTION = (
    "You are choosing the next step for an accessibility review of a small shop. "
    "Pick exactly one action from the options. FIX moves furniture and may only "
    "target problems where furniture_can_fix is true. RESCAN_AREA asks for a "
    "short follow-up scan and may only target findings where wants_another_look "
    "is true. ASK_OWNER needs one specific question. ESCALATE queues a problem "
    "for a professional. DONE ends the review. The application supplies eligible "
    "finding targets after the action is selected. Prioritize FIX for measured "
    "problems; unrelated evidence questions do not prevent testing furniture "
    "placements. Never repeat a scan request or owner question already issued."
)

ACTION_CRITERIA = {
    "FIX": "Move furniture to resolve one or more measured problems when furniture_can_fix is true.",
    "RESCAN_AREA": "Request a short follow-up scan for findings marked wants_another_look.",
    "ASK_OWNER": "Ask the owner for evidence that the scan cannot measure.",
    "ESCALATE": "Queue measured problems that furniture cannot resolve for a professional.",
    "DONE": "End the review when no eligible next step remains.",
}


def available_actions(state: RouterState) -> dict[str, str]:
    """Only offer work that can still change this run's outcome."""
    eligible = set()
    if state.fixable_finding_ids and state.fix_budget_left:
        eligible.add("FIX")
    if state.rescan_finding_ids and "RESCAN_AREA" not in state.actions_taken:
        eligible.add("RESCAN_AREA")
    if state.questions and "ASK_OWNER" not in state.actions_taken:
        eligible.add("ASK_OWNER")
    if any(f.id not in state.fixable_finding_ids for f in state.problems) and "ESCALATE" not in state.actions_taken:
        eligible.add("ESCALATE")
    return {action: rubric for action, rubric in ACTION_CRITERIA.items()
            if action in (eligible or {"DONE"})}

RESPONSE_PATHS = (
    ("data",),
    ("output",),
    ("result",),
    ("choices", 0, "message", "content"),
)
"""Where a structured answer tends to sit in a response envelope.

Checked in order, and the whole body is used when none of them match, so a
service that returns the object at the top level works without configuration.
"""

_NO_TYPESAFE_ANSWER = object()


@dataclass
class TypeSafeCallBudget:
    """One thread-safe, per-campaign ceiling on paid provider calls."""

    limit: int
    used: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.limit, bool) or not 1 <= self.limit <= 50_000:
            raise ValueError("TypeSafe call limit must be between 1 and 50000")
        if (
            isinstance(self.used, bool)
            or not isinstance(self.used, int)
            or not 0 <= self.used <= self.limit
        ):
            raise ValueError("TypeSafe calls used must be between zero and the limit")

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)

    def reserve(self) -> bool:
        with self._lock:
            if self.used >= self.limit:
                return False
            self.used += 1
            return True


class Transport(Protocol):
    def post(self, url: str, body: bytes, headers: dict[str, str]) -> bytes: ...


class UrllibTransport:
    """The standard library, so no lane inherits an HTTP dependency."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.timeout = timeout

    def post(self, url: str, body: bytes, headers: dict[str, str]) -> bytes:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()


def extract_payload(body: Any) -> Any:
    """Reach past a response envelope to the structured object inside it."""
    if isinstance(body, (str, bytes)):
        body = _load(body)
    if not isinstance(body, dict):
        return body
    choice = _typesafe_choice(body)
    if choice is not _NO_TYPESAFE_ANSWER:
        return {"action": choice}
    for path in RESPONSE_PATHS:
        found = _walk(body, path)
        if found is not None:
            nested_choice = _typesafe_choice(found)
            if nested_choice is not _NO_TYPESAFE_ANSWER:
                return {"action": nested_choice}
            return found
    return body


def _typesafe_choice(body: Any) -> Any:
    """Return the live System One action answer, if this is a TypeSafe body."""
    if isinstance(body, (str, bytes)):
        body = _load(body)
    if not isinstance(body, dict):
        return _NO_TYPESAFE_ANSWER
    answers = body.get("answers")
    if answers is None:
        for key in ("data", "output", "result"):
            found = _typesafe_choice(body.get(key))
            if found is not _NO_TYPESAFE_ANSWER:
                return found
        return _NO_TYPESAFE_ANSWER
    if not isinstance(answers, dict):
        return None
    answer = answers.get("action")
    if not isinstance(answer, dict):
        return None
    if answer.get("type") != "choice":
        return None
    return answer.get("choice")


def _load(raw: str | bytes) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw


def _walk(body: Any, path: tuple) -> Any:
    current = body
    for step in path:
        list_index = (
            isinstance(step, int)
            and isinstance(current, list)
            and len(current) > step
        )
        mapping_key = isinstance(current, dict) and step in current
        if list_index or mapping_key:
            current = current[step]
        else:
            return None
    return current


class TypeSafeRouter:
    provider = PROVIDER

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        path: str = DEFAULT_PATH,
        model: str | None = None,
        transport: Transport | None = None,
        budget: TypeSafeCallBudget | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get(API_KEY_ENV)
        self.base_url = (
            base_url or os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL
        ).rstrip("/")
        self.path = path or DEFAULT_PATH
        self.model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
        self.transport = transport or UrllibTransport()
        self.budget = budget

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    def request_body(self, state: RouterState) -> dict:
        return {
            "state": state.summary(),
            "model": self.model,
            "questions": {
                "action": {
                    "type": "choice",
                    "instructions": INSTRUCTION,
                    "criteria": available_actions(state),
                }
            },
        }

    @traced("router.typesafe")
    def decide(self, state: RouterState) -> Decision | Rejected:
        if not self.configured:
            return Rejected("typesafe_not_configured")
        raw = self.ask(self.request_body(state))
        if isinstance(raw, Rejected):
            return raw
        choice = _typesafe_choice(raw)
        if choice is _NO_TYPESAFE_ANSWER:
            return Rejected("typesafe_response_missing_answers")
        payload = _decision_payload({"action": choice}, state)
        decision = parse_decision(
            payload,
            state.findings,
            PROVIDER,
            fixable_finding_ids=state.fixable_finding_ids,
            rescan_finding_ids=state.rescan_finding_ids,
        )
        if isinstance(decision, Rejected):
            return decision
        return _authorize(decision, state)

    @traced("router.typesafe.systemone")
    def ask(self, body: dict) -> Any | Rejected:
        """One request and the answer it returned, as its own traced call.

        System One reports a confidence and a probability for every action it
        did not pick. A `Decision` has no room for either, and both are worth
        having when a loop did something surprising, so the answer is traced
        where it arrives rather than summarized after the fact.
        """
        raw = self._call(body)
        if isinstance(raw, Rejected):
            return raw
        return _load(raw) if isinstance(raw, (str, bytes)) else raw

    def _call(self, body: dict) -> bytes | Rejected:
        if self.budget is not None and not self.budget.reserve():
            return Rejected("typesafe_call_budget_exhausted")
        try:
            return self.transport.post(
                f"{self.base_url}{self.path}",
                json.dumps(body).encode("utf-8"),
                {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError):
            return Rejected("transport_error")


def _decision_payload(payload: Any, state: RouterState) -> Any:
    """Turn TypeSafe's one Choice answer into the contract's decision shape.

    System One answers a typed question and does not generate arbitrary arrays
    of UUIDs or free-form text. The router therefore chooses targets from the
    measured state after the action Choice, then validates the assembled
    payload with the same parser used by every router.
    """
    if not isinstance(payload, dict):
        return payload
    action = payload.get("action")
    if action not in ACTIONS:
        return payload

    decision = dict(payload)
    if action == "FIX":
        decision["target_finding_ids"] = [
            str(finding_id) for finding_id in state.fixable_finding_ids
        ]
    elif action == "RESCAN_AREA":
        decision["target_finding_ids"] = [
            str(finding_id) for finding_id in state.rescan_finding_ids
        ]
    elif action == "ASK_OWNER":
        if state.questions:
            finding = state.questions[0]
            decision["target_finding_ids"] = [str(finding.id)]
            decision["question"] = finding.title
    elif action == "ESCALATE":
        fixable = set(state.fixable_finding_ids)
        decision["target_finding_ids"] = [
            str(finding.id)
            for finding in state.problems
            if finding.id not in fixable
        ]
    return decision


def _authorize(decision: Decision, state: RouterState) -> Decision | Rejected:
    """The schema is not enough: the action still has to name something it may act on."""
    targets = set(decision.target_finding_ids)
    if decision.action == "FIX":
        return _authorize_fix(decision, state, targets)
    if decision.action == "RESCAN_AREA" and not targets <= set(state.rescan_finding_ids):
        return Rejected("rescan_targets_measured_finding")
    if decision.action == "ESCALATE" and not targets <= {f.id for f in state.problems}:
        return Rejected("escalate_targets_nonproblem")
    if decision.action not in available_actions(state):
        return Rejected("action_not_available")
    return decision


def _authorize_fix(
    decision: Decision, state: RouterState, targets: set
) -> Decision | Rejected:
    if not state.fix_budget_left:
        return Rejected("fix_budget_exhausted")
    if not targets <= set(state.fixable_finding_ids):
        return Rejected("fix_targets_unfixable_finding")
    return decision
