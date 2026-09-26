"""Turning a measurement into a card the owner reads.

Three decisions happen here and nowhere else: whether an answer is a problem, a
pass or a request; which sentences it carries; and what its id is. The id is
derived from the scan and the thing measured, so a finding keeps the same id
across passes and the router can name it.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from standardphysics_contracts import Finding, SceneGraph
from standardphysics_contracts.findings import Asks, Outcome

from .checks import roles
from .checks.observation import Observation
from .copy import FindingCopy, another_look, describe, request
from .rules import AgentRulePack, RuleSpec

FINDING_NAMESPACE = uuid.UUID("7b3c1f04-5e2a-4c6b-9d18-000000000002")

OUTCOME_ORDER: dict[Outcome, int] = {"problem": 0, "question": 1, "passes": 2}


def _key_text(key) -> str:
    if isinstance(key, (tuple, list)):
        return "(" + ",".join(_key_text(part) for part in key) + ")"
    return str(key)


def finding_id(scan_id: UUID, observation: Observation) -> UUID:
    key = observation.dedupe_key or (observation.rule_id,)
    return uuid.uuid5(FINDING_NAMESPACE, f"{scan_id}|{_key_text(key)}")


def resolve(
    observation: Observation, rule: RuleSpec, graph: SceneGraph
) -> tuple[Outcome, FindingCopy]:
    """A check resting on geometry we are unsure of becomes a request."""
    if not rule.measurable:
        return "question", describe(observation, rule)
    if observation.asks_for:
        return "question", request(rule)
    unsure = roles.needs_another_look(graph, observation.relied_on)
    if unsure:
        return "question", another_look([node.label for node in unsure])
    outcome: Outcome = "passes" if observation.satisfied else "problem"
    return outcome, describe(observation, rule)


def asks_of(observation: Observation, rule: RuleSpec, graph: SceneGraph) -> Asks | None:
    """What would answer this check, when it can't be settled from the scan. Mirrors `resolve`."""
    if not rule.measurable:
        return rule.evidence
    if observation.asks_for:
        return observation.asks_for
    if roles.needs_another_look(graph, observation.relied_on):
        return "another_look"
    return None


def to_finding(
    observation: Observation, rule: RuleSpec, graph: SceneGraph, scan_id: UUID
) -> Finding:
    outcome, text = resolve(observation, rule, graph)
    return Finding(
        id=finding_id(scan_id, observation),
        check_id=rule.id,
        outcome=outcome,
        title=text.title,
        detail=text.detail,
        fix=text.fix if outcome == "problem" else None,
        measured_inches=observation.measured_inches,
        required_inches=observation.required_inches,
        citation=rule.citation,
        locus=observation.locus,
        asks=asks_of(observation, rule, graph) if outcome == "question" else None,
    )


def _severity(finding: Finding) -> float:
    if finding.measured_inches is None or finding.required_inches is None:
        return float("inf")
    return abs(finding.required_inches - finding.measured_inches)


def _sort_key(finding: Finding) -> tuple:
    return (OUTCOME_ORDER[finding.outcome], -_severity(finding), finding.check_id)


def to_findings(
    observations: list[Observation],
    rules: AgentRulePack,
    graph: SceneGraph,
    scan_id: UUID,
) -> list[Finding]:
    """Problems first, then requests, then what passed."""
    findings = [
        to_finding(observation, rules.by_id(observation.rule_id), graph, scan_id)
        for observation in observations
    ]
    return sorted(findings, key=_sort_key)
