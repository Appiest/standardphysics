"""Typed contracts for ADA layout directives and typology routing.

A directive is a layout constraint for one kind of space. Its authority is the
2010 ADA Standards sections it names, never a court case. Cases appear only as
references that show a rule being enforced, and a reference counts only once a
person has read the opinion and signed it.
"""

from __future__ import annotations

from datetime import date, datetime
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


FixedRole = Literal["service_counter", "point_of_sale"]


class PrecedentTrigger(BaseModel):
    """When a scan's scene graph activates this directive."""

    model_config = ConfigDict(extra="forbid")

    space_typologies: list[SpaceTypology] = Field(
        ..., description="Space typologies this directive governs."
    )
    required_entities: list[str] = Field(
        ..., description="Entity labels that must exist in the scene (case-insensitive substring match)."
    )


class PrecedentQuerySpec(BaseModel):
    """A measurement the directive needs, and the ADA section that sets its limit."""

    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(..., description="Unique identifier for this query within the corpus.")
    target_role: str = Field(..., description="Target object role, e.g. 'dining_surface', 'service_counter'.")
    metric: Literal[
        "height_inches", "clear_width_inches", "clear_length_inches", "knee_clearance_inches", "reach_inches"
    ] = Field(..., description="Physical dimension being measured.")
    threshold: float = Field(..., description="Limit from the cited section.")
    comparison: Literal["at_most", "at_least"] = Field(..., description="Which side of the limit complies.")
    citation: str = Field(..., description="ADA section that sets the limit, e.g. 'ADA_2010_904.4.1'.")
    rule_id: str | None = Field(
        default=None,
        description="Rulepack rule that already measures this, or None when nothing measures it yet.",
    )


class PrecedentConstraintSpec(BaseModel):
    """Hard constraints on layout proposals for this kind of space."""

    model_config = ConfigDict(extra="forbid")

    fixed_roles: list[FixedRole] = Field(
        default_factory=list,
        description="Roles a proposal may not move, whatever the scan says about movability.",
    )
    solution_pattern: str = Field(..., description="Remedy pattern a proposal should follow.")
    requires_accessible_dining: bool = Field(
        default=False,
        description="Whether 226.1's share of dining surfaces must comply with 902.3.",
    )
    dispersed: bool = Field(
        default=False,
        description="Whether accessible surfaces must sit among the others (226.2).",
    )


class CaseReference(BaseModel):
    """A court opinion showing the directive's rule being enforced.

    References are illustration, not authority. `verified_by` stays None until a
    person has read the opinion and confirmed the citation and the holding.
    """

    model_config = ConfigDict(extra="forbid")

    citation: str = Field(..., description="Reporter citation, e.g. '81 F. Supp. 3d 876 (N.D. Cal. 2015)'.")
    case_name: str
    court: str
    decided: date
    docket_number: str
    source_url: str = Field(..., description="Where the opinion text can be read.")
    holding: str = Field(..., description="What the court decided that bears on this directive.")
    verified_by: str | None = None
    verified_at: datetime | None = None


class PrecedentDirective(BaseModel):
    """A layout constraint for one kind of space, grounded in ADA sections."""

    model_config = ConfigDict(extra="forbid")

    directive_id: str = Field(..., description="Stable identifier, e.g. 'accessible_dining_surfaces'.")
    title: str
    authority: list[str] = Field(
        ..., min_length=1, description="ADA sections this directive rests on, e.g. 'ADA_2010_226.1'."
    )
    trigger: PrecedentTrigger
    inspection_queries: list[PrecedentQuerySpec] = Field(default_factory=list)
    constraints: PrecedentConstraintSpec
    plain_english_warning: str
    case_references: list[CaseReference] = Field(default_factory=list)

    @property
    def verified_cases(self) -> list[CaseReference]:
        return [case for case in self.case_references if case.verified_by]


class PrecedentViolation(BaseModel):
    """A failure of a scene layout against a directive."""

    model_config = ConfigDict(extra="forbid")

    directive_id: str
    authority: str
    rule_broken: str
    detail: str
    target_node_id: str | None = None
