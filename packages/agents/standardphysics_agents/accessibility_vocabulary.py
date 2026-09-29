"""The closed vocabularies Jev is allowed to answer with, and the batch size limit."""

from __future__ import annotations

from typing import Literal

MAX_BATCH_ITEMS = 100

ObjectClass = Literal[
    "ramp", "stair", "curb", "threshold", "furniture", "unknown"
]
EvidenceAction = Literal["rescan", "tape_measurement", "professional_review"]
ReviewRoute = Literal["automatic", "reasoning_model", "human"]
GoalKind = Literal[
    "preserve_count",
    "do_not_move",
    "preserve_fixture",
    "minimize_disruption",
    "other",
]

OBJECT_CLASSES: dict[str, str] = {
    "ramp": "A sloped walking or rolling surface connecting different levels.",
    "stair": "One or more steps intended for foot travel between levels.",
    "curb": (
        "A raised edge separating adjacent surfaces, often at a sidewalk or "
        "parking area."
    ),
    "threshold": "A small level change at a door or opening.",
    "furniture": "A movable or fixed furnishing rather than a building level change.",
    "unknown": "The evidence does not distinguish the supplied classes.",
}

EVIDENCE_ACTIONS: dict[str, str] = {
    "rescan": (
        "The geometry or visual coverage is incomplete and another scan can answer it."
    ),
    "tape_measurement": (
        "A person can safely resolve the exact dimension with a tape or level."
    ),
    "professional_review": (
        "The issue needs technical or legal judgment, destructive inspection, "
        "or specialist equipment."
    ),
}

FAILURE_PATTERNS: dict[str, str] = {
    "narrow_route": "A traversable route is narrower than the tested clearance.",
    "collision": "The wheelchair or mobility envelope intersects geometry.",
    "turning_space": "There is not enough space to turn or change direction.",
    "reach": "A control, surface, or object cannot be approached or reached.",
    "level_change": "A ramp, curb, threshold, slope, or stair blocks travel.",
    "missing_evidence": (
        "The simulation cannot decide because geometry or metadata is absent."
    ),
    "labeling": "Contradictory or incorrect scene labels drive the failure.",
    "workflow": "The generated journey or task definition itself is invalid.",
    "other": "No supplied recurring pattern adequately describes the failure.",
}

SEMANTIC_FEATURES: dict[str, str] = {
    "mobility_barrier": (
        "The text describes difficulty moving through or using the space."
    ),
    "level_change": (
        "The text mentions a ramp, stair, curb, threshold, slope, or vertical transition."
    ),
    "route_obstruction": (
        "The text describes an aisle, path, or entrance blocked or narrowed by something."
    ),
    "door_issue": (
        "The text describes door width, force, hardware, swing, or maneuvering clearance."
    ),
    "turning_issue": "The text describes difficulty turning a wheelchair or mobility aid.",
    "reach_issue": (
        "The text describes a control, counter, item, or service point being hard to reach."
    ),
    "customer_impact": (
        "The text reports a customer being excluded, delayed, diverted, or needing assistance."
    ),
    "missing_measurement": (
        "The text signals that a dimension or observation needed for evaluation is absent."
    ),
    "fixed_fixture_constraint": (
        "The text says plumbing, walls, counters, or another fixed fixture cannot move."
    ),
    "movable_furniture": (
        "The text identifies chairs, tables, displays, or other furniture that may be rearranged."
    ),
}

