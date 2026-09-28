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
from .fittings import HeightChange, LoweredSection, Replacement

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


MAX_FIXTURE_MOVES = 8
"""The most built-ins one answer may move."""
CONSTRUCTION_FIELDS = ("wall_shifts", "fixture_moves", "height_changes", "replacements", "add_lowered_section")


class TrainingEdits(RoomEdits):
    """Furniture moves, plus the construction a room may need when furniture alone cannot clear it."""

    moves: list[FurnitureMove] = Field(default_factory=list, max_length=64)
    wall_shifts: list[WallShift] = Field(default_factory=list, max_length=len(SIDES))
    fixture_moves: list[FixtureMove] = Field(default_factory=list, max_length=MAX_FIXTURE_MOVES)
    height_changes: list[HeightChange] = Field(default_factory=list, max_length=16)
    replacements: list[Replacement] = Field(default_factory=list, max_length=16)
    add_lowered_section: list[LoweredSection] = Field(default_factory=list, max_length=4)


def parse_edits(completion: str) -> TrainingEdits | None:
    """The edits a completion proposes, or None when it is not valid edits JSON."""
    try:
        return TrainingEdits.model_validate(json.loads(_json_text(completion)))
    except (ValueError, ValidationError):
        return None


def _repeats(values: list) -> bool:
    return len(set(values)) != len(values)


def _construction_complaint(edits: RoomEdits) -> str | None:
    refitted = [change.node_id for change in getattr(edits, "height_changes", [])]
    refitted += [swap.node_id for swap in getattr(edits, "replacements", [])]
    complaints = (
        ("duplicate_wall_sides", [shift.side for shift in getattr(edits, "wall_shifts", [])]),
        ("duplicate_fixtures", [move.node_id for move in getattr(edits, "fixture_moves", [])]),
        ("duplicate_refits", refitted),
        ("duplicate_counter_sections", [section.counter_id for section in getattr(edits, "add_lowered_section", [])]),
    )
    return next((reason for reason, values in complaints if _repeats(values)), None)


def has_construction(edits: RoomEdits) -> bool:
    return any(getattr(edits, name, []) for name in CONSTRUCTION_FIELDS)


def edit_complaint(graph: SceneGraph, edits: RoomEdits) -> str | None:
    complaint = _construction_complaint(edits)
    if complaint or (has_construction(edits) and not edits.moves):
        return complaint
    return _edit_complaint(graph, edits)


def built_room(graph: SceneGraph, edits: RoomEdits) -> SceneGraph:
    """The room after every construction edit, before any furniture moves."""
    return build(graph, getattr(edits, "wall_shifts", []), getattr(edits, "fixture_moves", []),
                 heights=getattr(edits, "height_changes", []), replacements=getattr(edits, "replacements", []),
                 sections=getattr(edits, "add_lowered_section", []))


def apply_edits(graph: SceneGraph, edits: RoomEdits) -> SceneGraph:
    """The room after its construction, then its furniture moves."""
    return apply_moves(built_room(graph, edits), node_moves(edits))


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
    payload = edits.model_dump(mode="json")
    for construction in CONSTRUCTION_FIELDS:
        if not payload.get(construction):
            payload.pop(construction, None)
    return json.dumps(payload, separators=(",", ":"))


def combined(*parts: TrainingEdits) -> TrainingEdits:
    """One answer holding every edit of each part, in order."""
    fields = ("moves", *CONSTRUCTION_FIELDS)
    return TrainingEdits(**{name: [edit for part in parts for edit in getattr(part, name)] for name in fields})
