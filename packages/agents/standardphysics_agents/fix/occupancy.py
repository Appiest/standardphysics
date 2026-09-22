"""Occupant bodies and swept travel, with adjustable personal dimensions.

A route is not proven by joining endpoints: the person moving along it is a
solid body with a width, a length, a turning envelope and a personal reach.
These are screening assumptions about a person. They are deliberately separate
from the accessibility rule pack, which alone decides what the law requires;
changing a personal assumption never changes a legal threshold.

The disc that contains the footprint in every orientation has radius equal to
half the footprint diagonal. Sweeping that disc along the measured path is
conservative in the strongest sense: wherever the oriented body could touch
something while rolling or pivoting, the swept disc touches it too, so a clear
sweep passes and a colliding sweep genuinely collides. Sampling consecutive
points no further apart than half the radius makes the swept tube continuous,
so a thin obstacle can never tunnel between two distant samples, and a path
must never be argued from its endpoints alone.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from standardphysics_contracts import Vec3, to_meters

_KEEP_SENTINEL = object()
"""What `resize` uses to mean "leave this dimension as it was"."""


@dataclass(frozen=True)
class OccupantProfile:
    """Physical screening dimensions for one kind of occupant.

    All inch fields are positive and finite by construction. `personal_reach_
    inches` is the highest point this person can grasp while seated; it is a
    screening assumption about the person, never a legal maximum, and it does
    not appear in any rule.
    """

    id: str
    title: str
    body_width_inches: float
    body_length_inches: float
    turning_diameter_inches: float
    personal_reach_inches: float | None = None

    def __post_init__(self) -> None:
        values = {
            "body width": self.body_width_inches,
            "body length": self.body_length_inches,
            "turning diameter": self.turning_diameter_inches,
        }
        for name, value in values.items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"occupant {name} must be finite and positive")
        if self.personal_reach_inches is not None and (
            not math.isfinite(self.personal_reach_inches)
            or self.personal_reach_inches <= 0
        ):
            raise ValueError("personal reach must be finite and positive")

    @property
    def envelope_radius_inches(self) -> float:
        """Radius of the disc containing the footprint in every orientation."""
        return math.hypot(self.body_width_inches, self.body_length_inches) / 2

    @property
    def envelope_radius_meters(self) -> float:
        return to_meters(self.envelope_radius_inches)

    @property
    def travel_width_inches(self) -> float:
        """The widest the body needs when moving straight along a corridor."""
        return self.body_width_inches


MANUAL_WHEELCHAIR = OccupantProfile(
    id="manual-wheelchair",
    title="Manual wheelchair",
    body_width_inches=26.0,
    body_length_inches=43.0,
    turning_diameter_inches=60.0,
    personal_reach_inches=48.0,
)

POWER_WHEELCHAIR = OccupantProfile(
    id="power-wheelchair",
    title="Power wheelchair",
    body_width_inches=30.0,
    body_length_inches=48.0,
    turning_diameter_inches=72.0,
    personal_reach_inches=42.0,
)

BARIATRIC_WHEELCHAIR = OccupantProfile(
    id="bariatric-wheelchair",
    title="Bariatric wheelchair",
    body_width_inches=32.0,
    body_length_inches=48.0,
    turning_diameter_inches=72.0,
    personal_reach_inches=40.0,
)

WALKER_USER = OccupantProfile(
    id="walker-user",
    title="Walker user",
    body_width_inches=24.0,
    body_length_inches=30.0,
    turning_diameter_inches=54.0,
    personal_reach_inches=48.0,
)

SHORT_REACH = OccupantProfile(
    id="short-reach",
    title="Shorter stature or lower reach",
    body_width_inches=26.0,
    body_length_inches=43.0,
    turning_diameter_inches=60.0,
    personal_reach_inches=36.0,
)

DEFAULT_OCCUPANTS = (
    MANUAL_WHEELCHAIR,
    POWER_WHEELCHAIR,
    BARIATRIC_WHEELCHAIR,
    WALKER_USER,
    SHORT_REACH,
)
"""Named screening defaults, one coherent set per occupant kind.

They are starting assumptions, not fixed people: call `resize` for a real
user's dimensions. The defaults are never mutated by adjustment.
"""

OCCUPANTS = {profile.id: profile for profile in DEFAULT_OCCUPANTS}


def occupant(profile_id: str) -> OccupantProfile:
    """The named default, or a KeyError naming the available choices."""
    try:
        return OCCUPANTS[profile_id]
    except KeyError:
        raise KeyError(
            f"unknown occupant profile {profile_id!r}; choose from "
            f"{sorted(OCCUPANTS)}"
        ) from None


def resize(
    profile: OccupantProfile,
    *,
    body_width_inches: float | object = _KEEP_SENTINEL,
    body_length_inches: float | object = _KEEP_SENTINEL,
    turning_diameter_inches: float | object = _KEEP_SENTINEL,
    personal_reach_inches: float | None | object = _KEEP_SENTINEL,
) -> OccupantProfile:
    """A new profile with the same filename identity and new dimensions.

    The original profile is frozen; nothing here mutates a default. Personal
    reach adjusts freely (including to `None`, "not assumed") because it is a
    personal screening assumption, while the rule pack that holds any legal
    reach band is untouched by this call.
    """

    def pick(current, wanted):
        return current if wanted is _KEEP_SENTINEL else wanted

    return OccupantProfile(
        id=profile.id,
        title=profile.title,
        body_width_inches=pick(
            profile.body_width_inches, body_width_inches
        ),
        body_length_inches=pick(
            profile.body_length_inches, body_length_inches
        ),
        turning_diameter_inches=pick(
            profile.turning_diameter_inches, turning_diameter_inches
        ),
        personal_reach_inches=pick(
            profile.personal_reach_inches, personal_reach_inches
        ),
    )


def ensure_spacing(path: list[Vec3], radius_meters: float) -> list[Vec3]:
    """The path refined so consecutive points are at most half a radius apart.

    With that spacing the swept envelope is one continuous tube: every pair of
    consecutive discs overlaps, so passage between two samples is passage
    through the swept body, never a jump. A caller that measures only the
    endpoints of a corner would report a diagonal; this refinement keeps the
    sampled travel honest, and the collision layer then judges the chord
    against the envelope rather than letting endpoint distance pretend a
    corner was cut.
    """
    if radius_meters <= 0 or not path:
        return list(path)
    spacing = radius_meters / 2
    refined: list[Vec3] = []
    for start, end in zip(path, path[1:]):
        if not refined or refined[-1] != start:
            refined.append(start)
        distance = math.dist(
            (start.x, start.y), (end.x, end.y)
        )
        steps = max(int(math.ceil(distance / spacing)), 1)
        for step in range(1, steps + 1):
            fraction = step / steps
            refined.append(
                Vec3(
                    x=start.x + (end.x - start.x) * fraction,
                    y=start.y + (end.y - start.y) * fraction,
                    z=start.z + (end.z - start.z) * fraction,
                )
            )
    if not refined and path:
        refined.append(path[0])
    return refined


__all__ = [
    "BARIATRIC_WHEELCHAIR",
    "DEFAULT_OCCUPANTS",
    "MANUAL_WHEELCHAIR",
    "OCCUPANTS",
    "POWER_WHEELCHAIR",
    "SHORT_REACH",
    "WALKER_USER",
    "OccupantProfile",
    "ensure_spacing",
    "occupant",
    "resize",
]
