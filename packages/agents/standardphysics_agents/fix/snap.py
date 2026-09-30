"""Snap a free-form move onto the nearest spot the hard constraints allow.

A model that writes raw coordinates lands furniture inside walls, other pieces
and door swings. Rather than refusing the whole answer, each move is kept where
the model put it when that is legal, and otherwise nudged outward in rings
around its target until `violations` finds nothing wrong. A move with no legal
spot nearby is dropped and reported. Every kept move is checked together with
the ones kept before it, so the moves returned are legal as a set.

The idea of letting the model state intent and code repair the geometry is
inspired by LayoutVLM (Sun et al., CVPR 2025, arXiv:2412.02193). This is not
their differentiable optimizer: it is a small local search judged by the same
hard-constraint check that refuses answers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from standardphysics_contracts import NodeMove, SceneGraph, Vec3, to_meters
from standardphysics_pipeline import footprint
from standardphysics_pipeline.footprints import Polygon, touching

from .constraints import is_allowed
from .moves import apply_moves

RING_STEP_METERS = to_meters(2.0)
RING_COUNT = 9
"""Rings two inches apart, so a move is never nudged more than eighteen inches from where it was asked."""

RING_DIRECTIONS = 16


@dataclass(frozen=True)
class Snapped:
    kept: list[NodeMove]
    dropped: list[NodeMove] = field(default_factory=list)
    nudged_meters: dict = field(default_factory=dict)
    """How far each kept move ended up from where it was asked, by node id; zero when it was legal as asked."""


def _offsets():
    yield 0.0, 0.0
    for ring in range(1, RING_COUNT + 1):
        radius = ring * RING_STEP_METERS
        for step in range(RING_DIRECTIONS):
            angle = 2 * math.pi * step / RING_DIRECTIONS
            yield radius * math.cos(angle), radius * math.sin(angle)


def _nudged(move: NodeMove, dx: float, dy: float) -> NodeMove:
    delta = move.delta_translation
    return move.model_copy(update={"delta_translation": Vec3(x=delta.x + dx, y=delta.y + dy, z=delta.z)})


def nearest_legal(base: SceneGraph, kept: list[NodeMove], move: NodeMove,
                  avoid: Polygon | None = None) -> tuple[NodeMove, float] | None:
    """The legal move closest to `move`, given the moves already kept, and how far it was nudged.

    With `avoid`, a spot reaching into that patch of floor is not a place to land, so a piece pushed out of a space
    to clear it is never nudged back in.
    """
    for dx, dy in _offsets():
        trial = _nudged(move, dx, dy)
        candidate = apply_moves(base, [*kept, trial])
        if avoid is not None and touching(footprint(candidate.by_id(move.node_id)), avoid):
            continue
        if is_allowed(base, candidate):
            return trial, math.hypot(dx, dy)
    return None


def snap_moves(base: SceneGraph, moves: list[NodeMove], avoid: Polygon | None = None) -> Snapped:
    """Each move kept as asked, nudged to legal floor outside `avoid`, or dropped when nothing legal is near."""
    snapped = Snapped(kept=[])
    for move in moves:
        if any(kept.node_id == move.node_id for kept in snapped.kept):
            snapped.dropped.append(move)
            continue
        found = nearest_legal(base, snapped.kept, move, avoid)
        if found is None:
            snapped.dropped.append(move)
            continue
        legal, distance = found
        snapped.kept.append(legal)
        snapped.nudged_meters[move.node_id] = distance
    return snapped
