"""Typed contracts for actionable ADA case precedents and typology routing.

A precedent is not narrative background. It compiles into concrete geometric queries
and redesign constraints that bind both deterministic checkers and neural layout models.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SpaceTypology(str, Enum):
    """Categorical classification of room use and public accommodation occupancy."""

    QSR_BEVERAGE = "commercial.beverage.boba"
    RESTAURANT_DINING = "commercial.restaurant.dining"
    HOSPITALITY_LOUNGE = "commercial.hospitality.lounge"
    COMMERCIAL_RETAIL = "commercial.retail.mercantile"
    BUSINESS_OFFICE = "commercial.office.private"
    ASSEMBLY_PRESENTATION = "assembly.presentation_room"
    CIVIC_LIBRARY = "civic.library.reading_room"
    RESTROOM_FACILITY = "facility.restroom.single_user"


class PrecedentTrigger(BaseModel):
    """When a scan's scene graph activates this case precedent."""

    model_config = ConfigDict(extra="forbid")

    space_typologies: list[SpaceTypology] = Field(
        ..., description="Space typologies this precedent governs."
    )
    required_entities: list[str] = Field(
        ..., description="Entity labels that must exist in the scene (case-insensitive substring match)."
    )
    spatial_conditions: list[str] = Field(
        default_factory=list,
        description="Optional spatial flags, e.g. 'elevated_drink_surfaces_present'.",
    )


class PrecedentQuerySpec(BaseModel):
    """A geometric measurement required to verify compliance with this precedent."""

    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(..., description="Unique identifier for this query within the directive.")
    target_role: str = Field(..., description="Target object role or label, e.g. 'dining_surface', 'counter'.")
    metric: Literal["height_inches", "clear_width_inches", "knee_clearance_inches", "accessible_ratio", "reach_inches"] = Field(
        ..., description="Physical dimension or ratio being measured."
    )
    threshold: float = Field(..., description="Legal boundary value.")
    comparison: Literal["at_most", "at_least", "ratio_at_least"] = Field(
        ..., description="Direction of legal satisfaction."
    )
    citation: str = Field(..., description="Primary statutory basis, e.g. 'ADA_2010_904.4.1'.")


class PrecedentConstraintSpec(BaseModel):
    """Hard constraints imposed on layout generation to prevent repeating the case failure."""

    model_config = ConfigDict(extra="forbid")

    forbidden_moves: list[str] = Field(
        default_factory=list,
        description="Rules barring moving certain elements, e.g. 'do_not_move_fixed_bar_plumbing'.",
    )
    solution_pattern: str = Field(
        ..., description="Approved remedy pattern from the settlement/judgment."
    )
    minimum_accessible_percentage: float = Field(
        default=0.0, description="Mandatory ratio of accessible features (e.g. 0.05 for dining surfaces)."
    )
    anti_isolation: bool = Field(
        default=False,
        description="Whether accessible items must remain integrated into the primary customer zone.",
    )


class PrecedentDirective(BaseModel):
    """An executable landmark ADA case precedent."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(..., description="Court docket identifier, e.g. 'US-CAND-2021-CV-03481'.")
    landmark_citation: str = Field(..., description="Full legal citation.")
    title: str = Field(..., description="Descriptive title of the barrier vector.")
    year: int = Field(..., description="Year decided or filed.")
    jurisdiction: str = Field(..., description="Court or agency, e.g. 'N.D. Cal.', '9th Cir.', 'U.S.'.")
    trigger: PrecedentTrigger = Field(..., description="Conditions under which this precedent activates.")
    inspection_queries: list[PrecedentQuerySpec] = Field(
        default_factory=list, description="Mandatory checks for the measurement provider."
    )
    constraints: PrecedentConstraintSpec = Field(
        ..., description="Hard constraints on redesign proposals."
    )
    plain_english_warning: str = Field(
        ..., description="Plain English description of the violation risk for the owner."
    )
    verified_by: str = Field(..., description="Name of the human legal reviewer who confirmed the facts.")


class PrecedentViolation(BaseModel):
    """A failure of a scene layout against an actionable precedent directive."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    landmark_citation: str
    rule_broken: str
    detail: str
    target_node_id: str | None = None
