"""What the router is allowed to see when it picks the next action.

Findings, how many passes have run, how many fixes have been tried, and which
findings furniture can actually resolve. No geometry, no keys, and no way to
change a measurement.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from standardphysics_contracts import Finding, SceneGraph
from standardphysics_contracts.loop import RouterAction

from ..checks.roles import RoomKind, needs_another_look, room_kind
from ..rules import AgentRulePack

MAX_FIX_ATTEMPTS = 3
"""After three failed proposals the loop stops proposing. Plan section 10."""

ONE_SHOT_ACTIONS: frozenset[RouterAction] = frozenset({"RESCAN_AREA", "ASK_OWNER", "ESCALATE"})
"""Actions that ask a person for something, so once per layout is enough.

The local policy never repeats one on the layout it was taken on. The loop
holds every router to the same rule: only a FIX changes the layout, so asking
again before one has landed hands the same person the same request."""

REPEATED_ACTION = "repeats_an_action_already_taken_on_this_layout"
"""Why the loop refused an answer that breaks `ONE_SHOT_ACTIONS`."""


@dataclass(frozen=True)
class RouterState:
    findings: list[Finding]
    pass_number: int
    fix_attempts: int = 0
    fixable_finding_ids: tuple[UUID, ...] = ()
    """Problems with at least one movable thing causing them."""

    rescan_finding_ids: tuple[UUID, ...] = ()
    """Answers resting on geometry we want another look at."""

    unevaluated: tuple[str, ...] = ()
    actions_taken: tuple[RouterAction, ...] = ()
    """What this loop has already done, so it does not do it twice.

    Asking the owner the same question on every pass is not a loop, it is a
    stutter.
    """

    last_gate: dict | None = None
    """What the gate said about the most recent rearrangement: whether it was
    kept, problems and inches short before and after, and its reasons."""

    last_search: dict | None = None
    """What the most recent search measured, which hard constraints turned
    candidates away, and whether it found anything at all."""

    room_kind: RoomKind = "general"
    """A service business, a home, or a general room, from what the scan holds."""

    @property
    def last_action(self) -> RouterAction | None:
        return self.actions_taken[-1] if self.actions_taken else None

    @property
    def problems(self) -> list[Finding]:
        return [f for f in self.findings if f.outcome == "problem"]

    @property
    def questions(self) -> list[Finding]:
        return [f for f in self.findings if f.outcome == "question"]

    @property
    def fix_budget_left(self) -> int:
        return max(MAX_FIX_ATTEMPTS - self.fix_attempts, 0)

    def summary(self) -> dict:
        """The shape that goes over the wire."""
        return {
            "room_kind": self.room_kind,
            "pass_number": self.pass_number,
            "fix_attempts": self.fix_attempts,
            "fix_attempts_remaining": self.fix_budget_left,
            "last_action": self.last_action,
            "actions_taken": list(self.actions_taken),
            "unevaluated_rules": list(self.unevaluated),
            "last_gate": self.last_gate,
            "last_search": self.last_search,
            "findings": [self._finding_summary(f) for f in self.findings],
        }

    def _finding_summary(self, finding: Finding) -> dict:
        return {
            "id": str(finding.id),
            "check_id": finding.check_id,
            "outcome": finding.outcome,
            "title": finding.title,
            "measured_inches": finding.measured_inches,
            "required_inches": finding.required_inches,
            "citation": finding.citation.display(),
            "furniture_can_fix": finding.id in self.fixable_finding_ids,
            "wants_another_look": finding.id in self.rescan_finding_ids,
        }


def _movable_cause(
    finding: Finding, graph: SceneGraph, rules: AgentRulePack
) -> bool:
    """Whether moving furniture could clear this.

    Two conditions, and both matter. The rule has to be about where things are
    standing rather than about what they are, because no rearrangement makes a
    counter shorter. And at least one of the things causing it has to be
    movable.
    """
    if finding.locus is None:
        return False
    try:
        if not rules.by_id(finding.check_id).rearrangeable:
            return False
    except KeyError:
        return False
    return any(
        _is_movable(graph, node_id) for node_id in finding.locus.node_ids
    )


def _is_movable(graph: SceneGraph, node_id: UUID) -> bool:
    try:
        return graph.by_id(node_id).movable
    except KeyError:
        return False


def _wants_another_look(finding: Finding, graph: SceneGraph) -> bool:
    """A question about geometry we are not sure of wants another look.

    Thin coverage on a display case is a rescan. A door whose opening we
    measured, but not at 90 degrees, is a tape-measure request, and that must
    not jump the queue in front of a rearrangement that furniture can make.
    """
    if finding.outcome != "question" or finding.locus is None:
        return False
    return bool(needs_another_look(graph, finding.locus.node_ids))


def state_for(
    findings: list[Finding],
    graph: SceneGraph,
    rules: AgentRulePack,
    *,
    pass_number: int = 1,
    fix_attempts: int = 0,
    unevaluated: tuple[str, ...] = (),
    actions_taken: tuple[RouterAction, ...] = (),
    last_gate: dict | None = None,
    last_search: dict | None = None,
) -> RouterState:
    return RouterState(
        findings=findings,
        pass_number=pass_number,
        fix_attempts=fix_attempts,
        fixable_finding_ids=tuple(
            f.id
            for f in findings
            if f.outcome == "problem" and _movable_cause(f, graph, rules)
        ),
        rescan_finding_ids=tuple(
            f.id for f in findings if _wants_another_look(f, graph)
        ),
        unevaluated=unevaluated,
        actions_taken=actions_taken,
        last_gate=last_gate,
        last_search=last_search,
        room_kind=room_kind(graph),
    )
