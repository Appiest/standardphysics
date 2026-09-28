"""The pieces a replacement or a new counter section may bring in, with the dimensions they ship at.

A replacement keeps the replaced piece's centre and heading and takes the
catalog's size, so the checker measures the new piece exactly as it would a
scanned one. The label is the one the checks read: a replacement table has to
be a dining surface to count toward the 5 percent of ADA 2010 226.1.
"""

from __future__ import annotations

from dataclasses import dataclass

from standardphysics_contracts import to_meters


@dataclass(frozen=True)
class CatalogItem:
    name: str
    label: str
    length_inches: float
    depth_inches: float | None
    """None keeps the depth of the piece it replaces or the counter it is cut from."""
    top_inches: float
    knee_clearance_inches: float | None = None
    """The clear height under the top, ADA 2010 306.3; None where nobody pulls their knees under it."""

    @property
    def top_meters(self) -> float:
        return to_meters(self.top_inches)

    def as_prompt(self) -> dict:
        return {"name": self.name, "label": self.label, "length_inches": self.length_inches,
                "depth_inches": self.depth_inches, "top_inches": self.top_inches,
                "knee_clearance_inches": self.knee_clearance_inches}


ACCESSIBLE_TWO_TOP = CatalogItem("accessible_two_top", "Dining table", 30.0, 30.0, 30.0, 27.0)
ACCESSIBLE_FOUR_TOP = CatalogItem("accessible_four_top", "Dining table", 36.0, 36.0, 30.0, 27.0)
LOWERED_COUNTER_SECTION = CatalogItem("lowered_counter_section", "Lowered counter section", 36.0, None, 36.0)
"""ADA 2010 904.4.1: at least 36 inches long and no higher than 36 inches."""

CATALOG: dict[str, CatalogItem] = {
    item.name: item for item in (ACCESSIBLE_TWO_TOP, ACCESSIBLE_FOUR_TOP, LOWERED_COUNTER_SECTION)
}
