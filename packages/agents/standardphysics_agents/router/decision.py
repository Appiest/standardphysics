"""The router's answer, validated before it authorizes anything.

Structured output is only useful if something checks it. Every field is read
defensively and every contradiction is named, because this output picks which
branch of the loop runs next: a bad `FIX` moves a customer's furniture and a bad
`DONE` ends the report early.

Anything that does not validate comes back as `Rejected`, which no handler is
registered for, so a malformed answer authorizes nothing rather than falling
through to a default.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from standardphysics_contracts import Decision, Finding
from standardphysics_contracts.loop import RouterAction

from ..strict_schema import strict_schema

ACTIONS: frozenset[str] = frozenset(
    {"FIX", "RESCAN_AREA", "ASK_OWNER", "ESCALATE", "DONE"}
)

REQUIRES_TARGET: frozenset[str] = frozenset({"FIX", "RESCAN_AREA", "ESCALATE"})
"""Actions that have to say what they are about.

"Fix it" with nothing named is not an instruction, and acting on it would mean
choosing the target ourselves from something that never chose one.
"""

MAX_QUESTION_CHARACTERS = 400


@dataclass(frozen=True)
class Rejected:
    reason: str

    def __bool__(self) -> bool:
        return False


def action_schema() -> dict:
    """The schema the router is constrained to, generated from the contract.

    Handing over a schema written by hand means it drifts from `Decision` the
    first time either changes, and then valid output stops parsing. Strict, so
    a router that does support schema enforcement is held to the whole shape
    and one that does not is parsed the same way either way.
    """
    return strict_schema(Decision)


def _as_object(raw: Any) -> dict | Rejected:
    if raw is None:
        return Rejected("empty_response")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        return _decode(raw)
    return Rejected("not_an_object")


def _decode(raw: str | bytes) -> dict | Rejected:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    if not text.strip():
        return Rejected("empty_response")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return Rejected("not_json")
    return parsed if isinstance(parsed, dict) else Rejected("not_an_object")


def _as_action(payload: dict) -> RouterAction | Rejected:
    action = payload.get("action")
    if not isinstance(action, str):
        return Rejected("no_action")
    if action not in ACTIONS:
        return Rejected("unknown_action")
    return action  # type: ignore[return-value]


def _as_targets(payload: dict, findings: list[Finding]) -> list[UUID] | Rejected:
    raw = payload.get("target_finding_ids", [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        return Rejected("targets_not_a_list")
    known = {finding.id for finding in findings}
    targets: list[UUID] = []
    for item in raw:
        parsed = _as_uuid(item)
        if parsed is None:
            return Rejected("target_not_an_id")
        if parsed not in known:
            return Rejected("target_not_in_findings")
        targets.append(parsed)
    return targets


def _as_uuid(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _as_text(payload: dict, key: str) -> str | None | Rejected:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        return Rejected(f"{key}_not_text")
    if len(value) > MAX_QUESTION_CHARACTERS:
        return Rejected(f"{key}_too_long")
    return value


def _contradiction(
    decision: Decision,
    findings: list[Finding],
    *,
    fixable_finding_ids: Collection[UUID] | None,
    rescan_finding_ids: Collection[UUID] | None,
) -> str | None:
    if decision.action in REQUIRES_TARGET and not decision.target_finding_ids:
        return f"{decision.action.casefold()}_without_target"
    if decision.action == "ASK_OWNER" and not (decision.question or "").strip():
        return "ask_without_question"
    if decision.action == "DONE" and decision.target_finding_ids:
        return "done_with_targets"
    if decision.action == "FIX":
        problem_conflict = _fix_targets_problems(decision, findings)
        if problem_conflict:
            return problem_conflict
        if fixable_finding_ids is not None and any(
            target not in fixable_finding_ids
            for target in decision.target_finding_ids
        ):
            return "fix_targets_unfixable_finding"
    if decision.action == "RESCAN_AREA" and rescan_finding_ids is not None and any(
        target not in rescan_finding_ids for target in decision.target_finding_ids
    ):
        return "rescan_targets_finding_not_requesting_rescan"
    if decision.action == "ESCALATE":
        problems = {finding.id for finding in findings if finding.outcome == "problem"}
        if any(target not in problems for target in decision.target_finding_ids):
            return "escalate_targets_something_that_is_not_a_problem"
    return None


def _fix_targets_problems(decision: Decision, findings: list[Finding]) -> str | None:
    """A fix moves furniture, so it may only target something that failed."""
    problems = {finding.id for finding in findings if finding.outcome == "problem"}
    if any(target not in problems for target in decision.target_finding_ids):
        return "fix_targets_something_that_passed"
    return None


def parse_decision(
    raw: Any,
    findings: list[Finding],
    provider: str = "typesafe",
    *,
    fixable_finding_ids: Collection[UUID] | None = None,
    rescan_finding_ids: Collection[UUID] | None = None,
) -> Decision | Rejected:
    payload = _as_object(raw)
    if isinstance(payload, Rejected):
        return payload

    action = _as_action(payload)
    if isinstance(action, Rejected):
        return action

    targets = _as_targets(payload, findings)
    if isinstance(targets, Rejected):
        return targets

    question = _as_text(payload, "question")
    if isinstance(question, Rejected):
        return question

    rationale = _as_text(payload, "rationale")
    if isinstance(rationale, Rejected):
        return rationale

    decision = Decision(
        action=action,
        target_finding_ids=targets,
        question=question,
        rationale=rationale,
        provider=provider,
    )
    conflict = _contradiction(
        decision,
        findings,
        fixable_finding_ids=fixable_finding_ids,
        rescan_finding_ids=rescan_finding_ids,
    )
    return Rejected(conflict) if conflict else decision
