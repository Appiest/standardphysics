"""What each kind of edit costs in reward, so the cheapest legitimate fix ranks first.

The order is the order an owner would pay in: setting the register down on the
new lowered section, then sliding furniture, then changing a height or buying a
replacement piece, then relocating a built-in, then moving a wall. A fixture
move and a wall shift each carry a base price before their inches, so even a
one-inch wall shift costs more than a whole new table.

Height changes and new counter sections are priced per 36 inches of top they
touch, at least one run each, so lowering a whole ten-foot counter costs more
than cutting in the one 36 inch section ADA 2010 904.4.1 asks for.
"""

from __future__ import annotations

import math

from standardphysics_contracts import SceneGraph, to_inches, to_meters

CARRY_POINT_OF_SALE = 0.002
"""Per register or card reader set down on a new lowered section."""
FURNITURE_PER_METER = 0.03
"""Per metre a movable piece slides; a turn counts as `TURN_DISRUPTION_METERS`."""
HEIGHT_CHANGE_PER_RUN = 0.04
REPLACEMENT_PER_PIECE = 0.04
LOWERED_SECTION_PER_RUN = 0.04
RUN_INCHES = 36.0
FIXTURE_MOVE_BASE = 0.06
FIXTURE_MOVE_PER_INCH = 0.002
WALL_SHIFT_BASE = 0.08
WALL_SHIFT_PER_INCH = 0.005

MAX_FURNITURE_PENALTY = 0.15
MAX_CONSTRUCTION_PENALTY = 0.20


def runs(length_meters: float) -> int:
    """How many 36 inch runs of top a change touches, never fewer than one."""
    return max(1, math.ceil(to_inches(length_meters) / RUN_INCHES - 1e-6))


def furniture_price(disruption_meters: float) -> float:
    return min(MAX_FURNITURE_PENALTY, FURNITURE_PER_METER * disruption_meters)


def fixture_move_price(inches: float) -> float:
    return FIXTURE_MOVE_BASE + FIXTURE_MOVE_PER_INCH * inches


def wall_shift_price(inches: float) -> float:
    return WALL_SHIFT_BASE + WALL_SHIFT_PER_INCH * inches


def capped_construction(price: float) -> float:
    return min(MAX_CONSTRUCTION_PENALTY, price)


def _longest_side(room: SceneGraph, node_id) -> float:
    size = room.by_id(node_id).dimensions
    return max(size.x, size.y)


def construction_price(room: SceneGraph, edits) -> float:
    """The uncapped price of every construction edit in an answer, against the room it was proposed for."""
    sections = getattr(edits, "add_lowered_section", [])
    return round(
        sum(CARRY_POINT_OF_SALE * len(section.carry) for section in sections)
        + sum(LOWERED_SECTION_PER_RUN * runs(to_meters(section.length_inches)) for section in sections)
        + sum(HEIGHT_CHANGE_PER_RUN * runs(_longest_side(room, change.node_id))
              for change in getattr(edits, "height_changes", []))
        + REPLACEMENT_PER_PIECE * len(getattr(edits, "replacements", []))
        + sum(fixture_move_price(move.inches) for move in getattr(edits, "fixture_moves", []))
        + sum(wall_shift_price(shift.inches) for shift in getattr(edits, "wall_shifts", [])),
        6,
    )
