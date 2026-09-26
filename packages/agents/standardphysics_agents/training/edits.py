"""Model answers as `RoomEdits`, and layouts as the edits between them."""

from __future__ import annotations

import json
import math
import re

from pydantic import ValidationError
from standardphysics_contracts import NodeMove, SceneGraph, SceneNode
from standardphysics_pipeline.footprints import rotation_about_z

from ..redesign import FurnitureMove, RoomEdits, _edit_complaint, _moves_of

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


def parse_edits(completion: str) -> RoomEdits | None:
    """The edits a completion proposes, or None when it is not valid `RoomEdits` JSON."""
    try:
        return RoomEdits.model_validate(json.loads(_json_text(completion)))
    except (ValueError, ValidationError):
        return None


def edit_complaint(graph: SceneGraph, edits: RoomEdits) -> str | None:
    return _edit_complaint(graph, edits)


def node_moves(edits: RoomEdits) -> list[NodeMove]:
    return _moves_of(edits)


def yaw_degrees(node: SceneNode) -> float:
    cos_t, sin_t = rotation_about_z(node)
    return math.degrees(math.atan2(sin_t, cos_t))


def _turn_between(before: SceneNode, after: SceneNode) -> float:
    turn = yaw_degrees(after) - yaw_degrees(before)
    return (turn + 180.0) % 360.0 - 180.0


def _changes(before: SceneGraph, after: SceneGraph):
    originals = {node.id: node for node in before.nodes}
    for node in after.nodes:
        original = originals.get(node.id)
        if original is None or node.transform.m == original.transform.m:
            continue
        dx = node.transform.position.x - original.transform.position.x
        dy = node.transform.position.y - original.transform.position.y
        turn = _turn_between(original, node)
        if abs(dx) < MOVE_EPSILON_METERS and abs(dy) < MOVE_EPSILON_METERS and abs(turn) < TURN_EPSILON_DEGREES:
            continue
        yield node.id, dx, dy, turn


def edits_between(before: SceneGraph, after: SceneGraph) -> RoomEdits:
    """The floor slides and turns that take `before` to `after`, rounded to a centimetre and a degree."""
    return RoomEdits(moves=[
        FurnitureMove(node_id=node_id, dx=round(dx, 2), dy=round(dy, 2), rotation_degrees=float(round(turn)))
        for node_id, dx, dy, turn in _changes(before, after)
    ])


def moves_between(before: SceneGraph, after: SceneGraph) -> list[NodeMove]:
    """The unrounded moves that take `before` to `after`, for handing a scored layout back as edits."""
    return node_moves(RoomEdits(moves=[
        FurnitureMove(node_id=node_id, dx=dx, dy=dy, rotation_degrees=turn)
        for node_id, dx, dy, turn in _changes(before, after)
    ]))


def edits_json(edits: RoomEdits) -> str:
    return json.dumps(edits.model_dump(mode="json"), separators=(",", ":"))
