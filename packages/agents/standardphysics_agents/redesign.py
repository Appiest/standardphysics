"""OpenRouter proposes room edits; measured constraints decide whether to keep them."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from standardphysics_contracts import NodeMove, SceneGraph, Vec3, bounds_the_room

from .assess import assess
from .evaluation.gate import accepts
from .fix import apply_moves, violations
from .models import ModelAnswer, OpenRouter
from .router import Rejected
from .workflows import workflow_candidate_rejection


class FurnitureMove(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    node_id: UUID
    dx: float = Field(ge=-20, le=20)
    dy: float = Field(ge=-20, le=20)
    rotation_degrees: float = Field(ge=-180, le=180)


class RoomEdits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    moves: list[FurnitureMove] = Field(max_length=64)


@dataclass(frozen=True)
class RedesignResult:
    graph: SceneGraph | None
    model: str | None
    accepted: bool
    reasons: tuple[str, ...]


INSTRUCTION = (
    "Propose a furniture arrangement that improves at least one actionable measured accessibility issue. "
    "Use `actionable_failures` (blocked customer routes) and `actionable_rule_problems` (rule problems that "
    "involve movable objects) as the authoritative issues furniture can address; each item names the movable "
    "object IDs. `route_trials`, when present, summarizes route trials that already ran on this room: its "
    "`kept_moves` are already applied to `room`, and `failing_workflows` shows which journeys still failed "
    "and what blocked them. Build on those kept moves rather than undoing them. "
    "Use only IDs in `movable_objects`. Return floor translations in meters and "
    "rotations in degrees for one to four existing movable objects. "
    "Preserve every object's measured size, inventory, fixed fixtures, walls and doors. "
    "Do not move an object merely because an unlocalized raw-mesh collision exists. Do not return a no-op move. "
    "Do not infer that an attractive rendering is legally compliant. Return no moves if `actionable_failures` "
    "and `actionable_rule_problems` are both empty or the evidence does not support a safe improvement. "
    "The application remeasures every route and rule before accepting edits."
)


def propose_redesign(graph, workflows, profiles, feedback, measure, *, rules, ledger, model=None, collision_index=None) -> RedesignResult:
    client = model or OpenRouter()
    movable_objects = [
        {
            "id": str(node.id),
            "label": node.label,
            "position": node.transform.position.model_dump(mode="json"),
            "dimensions": node.dimensions.model_dump(mode="json"),
        }
        for node in graph.nodes
        if not bounds_the_room(node) and node.movable
    ]
    answer = client.structured(INSTRUCTION, {
        "room": graph.model_dump(mode="json"),
        "movable_objects": movable_objects,
        "actionable_failures": [
            failure
            for item in feedback
            for failure in item.get("actionable_failures", [])
        ],
        "actionable_rule_problems": [
            problem
            for item in feedback
            for problem in item.get("actionable_rule_problems", [])
        ],
        "route_trials": next((item["route_trials"] for item in feedback if "route_trials" in item), None),
        "evidence_gaps": [
            gap
            for item in feedback
            for gap in item.get("evidence_gaps", [])
        ],
        "workflow_feedback": feedback,
        "verified_rules": [rule.model_dump(mode="json") for rule in rules.enabled(ledger, max_tier=3)],
    }, RoomEdits.model_json_schema(), "room_furniture_edits")
    if isinstance(answer, Rejected):
        return RedesignResult(None, client.model, False, (answer.reason,))
    return validate_redesign(graph, answer, workflows, profiles, measure, rules=rules, ledger=ledger, collision_index=collision_index)


def _edit_complaint(graph, edits: RoomEdits) -> str | None:
    """Why these edits cannot be applied, or None when they can."""
    ids = [edit.node_id for edit in edits.moves]
    if not ids:
        return "no_supported_furniture_move"
    if len(ids) != len(set(ids)):
        return "duplicate_objects"
    if not set(ids) <= {node.id for node in graph.nodes}:
        return "unknown_objects"
    if all(edit.dx == 0 and edit.dy == 0 and edit.rotation_degrees == 0 for edit in edits.moves):
        return "no_op_moves"
    return None


def _moves_of(edits: RoomEdits) -> list[NodeMove]:
    return [
        NodeMove(
            node_id=edit.node_id,
            delta_translation=Vec3(x=edit.dx, y=edit.dy, z=0),
            delta_rotation_z_degrees=edit.rotation_degrees,
        )
        for edit in edits.moves
    ]


def _scored_against_workflows(graph, candidate, workflows, measure, rules, ledger):
    """(reasons to reject, whether anything measurably improved).

    Every workflow has to hold: one that gets worse rejects the candidate, and
    at least one has to actually improve or there is no reason to move anything.
    """
    improved = False
    for workflow in workflows:
        before = assess(graph, workflow.scenario, measure, rules=rules, ledger=ledger, max_tier=3)
        after = assess(candidate, workflow.scenario, measure, rules=rules, ledger=ledger, max_tier=3)
        gate = accepts(before, after, require_improvement=False)
        if not gate:
            return gate.reasons, improved
        improved = improved or bool(accepts(before, after))
    return None, improved


def validate_redesign(
    graph, answer: ModelAnswer, workflows, profiles, measure, *, rules, ledger, collision_index=None
) -> RedesignResult:
    def rejected(*reasons: str) -> RedesignResult:
        return RedesignResult(None, answer.model, False, tuple(reasons))

    try:
        edits = RoomEdits.model_validate(answer.payload)
    except ValidationError:
        return rejected("invalid_model_edits")

    complaint = _edit_complaint(graph, edits)
    if complaint:
        return rejected(complaint)

    candidate = apply_moves(graph, _moves_of(edits))
    broken = violations(graph, candidate)
    if broken:
        return rejected(*sorted({item.kind for item in broken}))
    if not rules.enabled(ledger, max_tier=3):
        return rejected("no_verified_rules")

    reasons, improved = _scored_against_workflows(graph, candidate, workflows, measure, rules, ledger)
    if reasons:
        return RedesignResult(None, answer.model, False, reasons)

    regression = workflow_candidate_rejection(
        graph, candidate, workflows=workflows, profiles=profiles, measure=measure, collision_index=collision_index
    )
    if regression or not improved:
        return rejected(regression or "nothing_measurable_improved")
    return RedesignResult(candidate, answer.model, True, ())
