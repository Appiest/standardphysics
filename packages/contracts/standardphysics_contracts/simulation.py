"""Bounded screening jobs against an immutable room and route snapshot."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .geometry import Vec3
from .scene import SceneGraph


class RebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: int = Field(ge=0)


class SimulationRequest(RebuildRequest):
    samples: int = Field(default=1000, ge=1, le=10000)
    max_workers: int = Field(default=4, ge=1, le=1000)
    router: Literal["local", "typesafe"] = "local"
    refine_with_astra: bool = False
    typesafe_call_limit: int = Field(default=3000, ge=1, le=50000)
    astra_rounds: int = Field(default=4, ge=1, le=8)
    exhaustive_evaluations: int = Field(default=0, ge=0, le=5_000_000)

    @model_validator(mode="after")
    def enough_budget_for_selected_models(self) -> SimulationRequest:
        reserved = (self.astra_rounds if self.refine_with_astra else 0) + (
            9 if self.exhaustive_evaluations else 0
        )
        if self.router == "typesafe":
            reserved += 1
        if self.typesafe_call_limit < reserved:
            raise ValueError(
                "typesafe_call_limit is too small for the selected bounded campaigns"
            )
        if 0 < self.exhaustive_evaluations < 40:
            raise ValueError("exhaustive_evaluations must be zero or at least 40")
        return self


class PhysicsObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    kind: Literal[
        "surface_slope",
        "uncontrolled_roll",
        "wheelchair_tip",
        "level_change",
        "stair_or_step",
        "turning",
    ]
    status: Literal["clear", "potential_barrier", "needs_measurement"]
    title: str
    measured_value: float | None = None
    reference_value: float | None = None
    unit: str | None = None
    source: Literal["lidar_mesh", "scene_graph", "route_geometry"]
    point: Vec3 | None = None
    node_ids: list[UUID] = []


class PhysicsRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    purpose: Literal["customer_access", "evacuation", "seat_to_cashier"]
    origin_node_id: UUID
    destination_node_id: UUID
    reachable: bool
    distance_inches: float | None = Field(default=None, ge=0)
    clear_width_inches: float | None = Field(default=None, ge=0)
    blocking_node_ids: list[UUID] = []


class EnvironmentPhysicsResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    resolution_inches: float = Field(gt=0)
    mesh_triangles_checked: int = Field(ge=0)
    surface_samples: int = Field(ge=0)
    observations: list[PhysicsObservation]
    routes: list[PhysicsRoute]
    exits_found: int = Field(ge=0)
    seats_found: int = Field(ge=0)
    cashiers_found: int = Field(ge=0)
    limitations: list[str]


class AdaptiveRoundResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round: int = Field(ge=1)
    base_graph_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    astra_model: str | None = None
    accepted: bool
    reasons: list[str]
    jev_preferred_candidate: str | None = None


class SimulationFeedback(BaseModel):
    workflow_title: str
    profile_title: str
    trials: int
    passed_trials: int
    clearance_failure_trials: int
    floor_plan_collision_trials: int
    mesh_collision_trials: int
    unreachable_interaction_trials: int
    needs_measurement_trials: int
    blocking_node_ids: list[str]


class SimulationResult(BaseModel):
    total_runs: int
    completed_runs: int
    rejected_runs: int
    unique_layouts: int
    action_counts: dict[str, int]
    rejection_counts: dict[str, int]
    feedback: list[SimulationFeedback]
    recommended_graph: SceneGraph | None
    rules_checked: int
    rules_total: int
    preview: bool
    mesh_checked: bool
    loop_cycles: int = Field(default=0, ge=0)
    violating_trials: int = Field(default=0, ge=0)
    ada_rule_violations: int = Field(default=0, ge=0)
    converged: bool = False
    redesign_model: str | None = None
    redesign_accepted: bool = False
    redesign_reasons: list[str] = []
    typesafe_calls: int = Field(default=0, ge=0)
    astra_calls: int = Field(default=0, ge=0)
    adaptive_rounds: list[AdaptiveRoundResult] = []
    physics: EnvironmentPhysicsResult | None = None
    exhaustive_evaluations: int = Field(default=0, ge=0)
    exhaustive_outcomes: dict[str, int] = {}
    limitations: list[str]

    @model_validator(mode="after")
    def convergence_requires_zero_violations(self) -> SimulationResult:
        if self.violating_trials > self.total_runs:
            raise ValueError("violating_trials cannot exceed total_runs")
        if self.converged and self.loop_cycles == 0:
            raise ValueError("converged results must come from a completed loop batch")
        if self.converged and (self.violating_trials or self.ada_rule_violations):
            raise ValueError("converged results must have zero violations")
        return self


class SimulationStatus(BaseModel):
    base_revision: int
    state: Literal["queued", "running", "done", "failed"]
    router: Literal["local", "typesafe"]
    samples: int
    completed: int
    cycle: int = Field(default=0, ge=0)
    candidate_graph: SceneGraph | None = None
    typesafe_call_limit: int = 0
    exhaustive_evaluations: int = 0
    error: str | None = None
    result: SimulationResult | None = None


class ReplayChapter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seconds: float = Field(ge=0, allow_inf_nan=False)
    task: str = Field(min_length=1)
    evaluation: int = Field(ge=1)
    outcome: Literal["route_blocked", "out_of_reach", "route_and_reach_fit"]


class SimulationReplay(BaseModel):
    """A saved functional campaign, separate from live legal screening jobs."""

    model_config = ConfigDict(extra="forbid")
    scan_id: UUID
    revision: int = Field(ge=0)
    graph_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    video_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluations: int = Field(ge=1)
    unique_layouts: int = Field(ge=1)
    connectivity_builds: int = Field(ge=1)
    typesafe_calls: int = Field(ge=0)
    task_source: str
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    selection: str
    chapters: list[ReplayChapter] = Field(min_length=1)
    limitations: list[str]
