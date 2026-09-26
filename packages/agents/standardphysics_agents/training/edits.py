"""Model answers as `RoomEdits`, and layouts as the edits between them."""

from __future__ import annotations

import json
import math
import re

from pydantic import Field, ValidationError
from standardphysics_contracts import NodeMove, SceneGraph, SceneNode
from standardphysics_pipeline.footprints import rotation_about_z

from ..fix import apply_moves
from ..redesign import FurnitureMove, RoomEdits, _edit_complaint, _moves_of
from .construction import SIDES, FixtureMove, WallShift, build

THINKING = re.compile(r"<think>.*?</think>", re.DOTALL)
FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

MOVE_EPSILON_METERS = 0.005
TURN_EPSILON_DEGREES = 0.5


def _json_text(completion: str) -> str:
    visible = THINKING.sub("", completion).strip()
    fenced = FENCE.search(visible)
    if fenced:
        return fenced.group(1).strip()
    start, end = visible.find("{"), visible.rfind("}")
    return visible[start:end + 1] if start >= 0 and end > start else visible


class TrainingEdits(RoomEdits):
    """Furniture moves, plus the construction a room may need when furniture alone cannot clear it."""

    moves: list[FurnitureMove] = Field(default_factory=list, max_length=64)
    wall_shifts: list[WallShift] = Field(default_factory=list, max_length=len(SIDES))
    fixture_moves: list[FixtureMove] = Field(default_factory=list, max_length=8)


def parse_edits(completion: str) -> TrainingEdits | None:
    """The edits a completion proposes, or None when it is not valid edits JSON."""
    try:
        return TrainingEdits.model_validate(json.loads(_json_text(completion)))
    except (ValueError, ValidationError):
        return None


def edit_complaint(graph: SceneGraph, edits: RoomEdits) -> str | None:
    shifts = getattr(edits, "wall_shifts", [])
    fixtures = getattr(edits, "fixture_moves", [])
    if len({shift.side for shift in shifts}) != len(shifts):
        return "duplicate_wall_sides"
    if len({move.node_id for move in fixtures}) != len(fixtures):
        return "duplicate_fixtures"
    if (shifts or fixtures) and not edits.moves:
        return None
    return _edit_complaint(graph, edits)


def apply_edits(graph: SceneGraph, edits: RoomEdits) -> SceneGraph:
    """The room after its construction, then its furniture moves."""
    built = build(graph, getattr(edits, "wall_shifts", []), getattr(edits, "fixture_moves", []))
    return apply_moves(built, node_moves(edits))


def node_moves(edits: RoomEdits) -> list[NodeMove]:
    return _moves_of(edits)


def yaw_degrees(node: SceneNode) -> float:
    cos_t, sin_t = rotation_about_z(node)
    return math.degrees(math.atan2(sin_t, cos_t))


def _turn_between(before: SceneNode, after: SceneNode) -> float:
    turn = yaw_degrees(after) - yaw_degrees(before)
    return (turn + 180.0) % 360.0 - 180.0


def edits_between(before: SceneGraph, after: SceneGraph) -> RoomEdits:
    """The floor slides and turns that take `before` to `after`, rounded to a centimetre and a degree."""
    originals = {node.id: node for node in before.nodes}
    moves = []
    for node in after.nodes:
        original = originals.get(node.id)
        if original is None or node.transform.m == original.transform.m:
            continue
        dx = node.transform.position.x - original.transform.position.x
        dy = node.transform.position.y - original.transform.position.y
        turn = _turn_between(original, node)
        if abs(dx) < MOVE_EPSILON_METERS and abs(dy) < MOVE_EPSILON_METERS and abs(turn) < TURN_EPSILON_DEGREES:
            continue
        moves.append(FurnitureMove(node_id=node.id, dx=round(dx, 2), dy=round(dy, 2),
                                   rotation_degrees=float(round(turn))))
    return RoomEdits(moves=moves)


def edits_json(edits: RoomEdits) -> str:
    payload = edits.model_dump(mode="json")
    for construction in ("wall_shifts", "fixture_moves"):
        if not payload.get(construction):
            payload.pop(construction, None)
    return json.dumps(payload, separators=(",", ":"))
