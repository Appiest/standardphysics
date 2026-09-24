"""Small collision corrections for a model's otherwise useful move."""

from __future__ import annotations

import math

from standardphysics_contracts import NodeMove, SceneGraph, Vec3

from ..fix import apply_moves, violations
from ..redesign import FurnitureMove, RoomEdits
from .checker import TrainingChecker
from .edits import edits_json
from .reward import score_completion

SNAP_STEP_METERS = 0.05
SNAP_MAX_METERS = 0.30
"""At most about 12 inches: enough for a small overlap, only one fifth of the 60 inch travel cap."""

_DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1),
               (1, 1), (1, -1), (-1, 1), (-1, -1))


def _completion(moves: list[NodeMove]) -> str:
    return edits_json(RoomEdits(moves=[FurnitureMove(
        node_id=move.node_id, dx=move.delta_translation.x, dy=move.delta_translation.y,
        rotation_degrees=move.delta_rotation_z_degrees,
    ) for move in moves]))


def _nudged(move: NodeMove, dx: float, dy: float) -> NodeMove:
    old = move.delta_translation
    return move.model_copy(update={"delta_translation": Vec3(x=old.x + dx, y=old.y + dy, z=old.z)})


def _offsets():
    for step in range(1, round(SNAP_MAX_METERS / SNAP_STEP_METERS) + 1):
        distance = step * SNAP_STEP_METERS
        for x, y in _DIRECTIONS:
            scale = distance / math.hypot(x, y)
            yield x * scale, y * scale


def _collision_count(room: SceneGraph, moves: list[NodeMove]) -> int | None:
    broken = violations(room, apply_moves(room, moves))
    failures = [item.kind for item in broken]
    if any(failure != "collided" for failure in failures):
        return None
    return len(broken)


def _clear_one(room: SceneGraph, moves: list[NodeMove], node_id) -> list[NodeMove] | None:
    before = _collision_count(room, moves)
    if before is None:
        return None
    for dx, dy in _offsets():
        trial = [_nudged(move, dx, dy) if str(move.node_id) == node_id else move for move in moves]
        remaining = _collision_count(room, trial)
        if remaining is not None and remaining < before:
            return trial
    return None


def snap_collisions(room: SceneGraph, moves: list[NodeMove], checker: TrainingChecker) -> list[NodeMove] | None:
    """Translate colliding moved pieces only; accept only if every hard rule and the gate still pass."""
    if any(move.node_id in checker.pinned for move in moves):
        return None
    broken = violations(room, apply_moves(room, moves))
    failures = [item.kind for item in broken]
    if not failures or any(failure != "collided" for failure in failures):
        return None
    corrected = moves
    for node_id in dict.fromkeys(item.node_id for item in broken):
        corrected = _clear_one(room, corrected, node_id)
        if corrected is None:
            return None
    if not score_completion(_completion(corrected), room, checker).gate_accepts:
        return None
    return corrected
