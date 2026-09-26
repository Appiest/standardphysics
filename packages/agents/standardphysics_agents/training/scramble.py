"""Bad but buildable layouts of one scanned room, for training.

Each variant slides and turns a few pieces of the room's own furniture, and is
kept only when it passes every hard constraint against the room as scanned, so
each one is a layout somebody could really have. Variants with nothing a
rearrangement could fix teach nothing and are dropped.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass

from standardphysics_contracts import NodeMove, SceneGraph, SceneNode, Vec3, bounds_the_room

from ..fix import apply_moves, violations
from ..fix.moves import floor_height, rests_on_something
from .checker import TrainingChecker

MAX_SLIDE_METERS = 1.2
TURNS = (0.0, 0.0, 0.0, 15.0, -15.0, 30.0, -30.0, 45.0, -45.0, 90.0, -90.0, 180.0)
MAX_PIECES = 3
ATTEMPTS_PER_VARIANT = 40

LIGHT_SLIDE_METERS = 0.6
LIGHT_TURNS = (0.0, 0.0, 0.0, 0.0, 15.0, -15.0, 30.0, -30.0, 90.0, -90.0)
"""A light scramble: one to three pieces, each slid at most 0.6 m and turned by a
familiar angle, so putting them back is a tidy-up rather than a rebuild."""


@dataclass(frozen=True)
class Displacement:
    slide: float = MAX_SLIDE_METERS
    turns: tuple[float, ...] = TURNS
    pieces: int = MAX_PIECES


LIGHT = Displacement(LIGHT_SLIDE_METERS, LIGHT_TURNS)
SHUFFLE = Displacement(1.5, TURNS, 6)
"""A shuffle for benchmarks: up to six pieces, each slid up to 1.5 m and turned by any angle in `TURNS`."""


@dataclass(frozen=True)
class Variant:
    name: str
    graph: SceneGraph
    fixable: tuple[str, ...]
    """Check ids of the furniture-fixable problems the variant has more of than the scanned room."""


def floor_furniture(graph: SceneGraph) -> list[SceneNode]:
    """Movable pieces standing on the floor, which are the ones a scramble may push."""
    floor_z = floor_height(graph)
    return [
        node for node in graph.nodes
        if node.movable and not bounds_the_room(node) and not rests_on_something(node, floor_z)
    ]


def random_moves(pieces: list[SceneNode], rng: random.Random, how: Displacement = Displacement()) -> list[NodeMove]:
    chosen = rng.sample(pieces, rng.randint(1, min(how.pieces, len(pieces))))
    return [
        NodeMove(
            node_id=node.id,
            delta_translation=Vec3(x=rng.uniform(-how.slide, how.slide), y=rng.uniform(-how.slide, how.slide), z=0.0),
            delta_rotation_z_degrees=rng.choice(how.turns),
        )
        for node in chosen
    ]


def _fixable_counts(graph: SceneGraph, checker: TrainingChecker) -> Counter:
    return Counter(finding.check_id for finding in checker.fixable_problems(checker.assess(graph)))


def added_problems(candidate: SceneGraph, already: Counter, checker: TrainingChecker) -> tuple[str, ...]:
    """Check ids the candidate has more of than the room it came from.

    A problem the room already has is not one a rearrangement back to the room
    can fix, so a variant carrying only those teaches nothing.
    """
    now = _fixable_counts(candidate, checker)
    return tuple(sorted(check for check, count in now.items() if count > already.get(check, 0)))


def _one_variant(scanned: SceneGraph, start: SceneGraph, rng: random.Random, checker: TrainingChecker,
                 how: Displacement, already: Counter):
    pieces = floor_furniture(start)
    if not pieces:
        return None
    for _ in range(ATTEMPTS_PER_VARIANT):
        candidate = apply_moves(start, random_moves(pieces, rng, how))
        if violations(scanned, candidate):
            continue
        added = added_problems(candidate, already, checker)
        if added:
            return candidate, added
    return None


def scramble(
    scanned: SceneGraph, checker: TrainingChecker, count: int, *, seed: int = 0, starts: list[SceneGraph] | None = None,
    how: Displacement = Displacement(),
) -> list[Variant]:
    """Up to `count` distinct variants. `starts` are layouts to scramble from, the scanned one by default."""
    origins = starts or [scanned]
    already = _fixable_counts(scanned, checker)
    found: list[Variant] = []
    for index in range(count):
        rng = random.Random(seed * 100_003 + index)
        made = _one_variant(scanned, origins[index % len(origins)], rng, checker, how, already)
        if made is None:
            continue
        graph, fixable = made
        found.append(Variant(f"v{index:03d}", graph.model_copy(update={"revision": scanned.revision}), fixable))
    return found
