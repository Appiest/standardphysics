"""How high a fixture sits, read off its measured box, and which side of a band that falls.

Several provisions give a height as a range: a grab bar's top between 33 and
36 inches, a seat between 17 and 19. Each reads the same way, so the reading
lives here once rather than as a pair of comparisons in each check.
"""

from __future__ import annotations

from standardphysics_contracts import SceneNode, to_inches

from .clear_floor import at_least, at_most


def top_inches(node: SceneNode) -> float:
    return to_inches(node.transform.position.z + node.dimensions.z / 2)


def bottom_inches(node: SceneNode) -> float:
    return to_inches(node.transform.position.z - node.dimensions.z / 2)


def band_reason(measured: float, low: float | None, high: float) -> str:
    """`measured` inside the band, or which end it missed."""
    if low is not None and not at_least(measured, low):
        return "too_low"
    if not at_most(measured, high):
        return "too_high"
    return "measured"


def band_limit(reason: str, low: float | None, high: float) -> float:
    """The end of the band a measurement has to meet, given which end it missed."""
    return low if reason == "too_low" and low is not None else high
