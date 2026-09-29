"""Measuring one workflow: every leg of its route and every object it must reach."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from standardphysics_contracts import (
    LidarMesh,
    MeasurementProvider,
    SceneGraph,
    Vec3,
    to_inches,
)

from .mesh_collision import MeshCollisionIndex
from .workflow_definitions import FunctionalProfile, Interaction, RoutePose, Workflow


@dataclass(frozen=True)
class WorkflowLeg:
    origin: str
    destination: str
    reachable: bool
    measured_width_inches: float
    required_width_inches: float
    meets_clearance: bool
    floor_plan_collision: bool
    mesh_collision: bool
    collision_free: bool
    blocking_node_ids: tuple[UUID, ...]
    route: tuple[RoutePose, ...]

    @property
    def passed(self) -> bool:
        return self.collision_free and self.meets_clearance


@dataclass(frozen=True)
class InteractionEvaluation:
    interaction: Interaction
    needs_measurement: bool
    approach_collision_free: bool | None
    measured_height_inches: float | None
    maximum_reach_inches: float | None
    height_reachable: bool | None
    reachable: bool | None


@dataclass(frozen=True)
class WorkflowEvaluation:
    workflow: Workflow
    profile: FunctionalProfile
    legs: tuple[WorkflowLeg, ...]
    interactions: tuple[InteractionEvaluation, ...]

    @property
    def passed(self) -> bool:
        return (
            all(leg.passed for leg in self.legs)
            and all(item.reachable is True for item in self.interactions)
        )


def evaluate_workflow(
    graph: SceneGraph,
    workflow: Workflow,
    profile: FunctionalProfile,
    measure: MeasurementProvider,
    *,
    lidar_mesh: LidarMesh | None = None,
    collision_index: MeshCollisionIndex | None = None,
) -> WorkflowEvaluation:
    """Measure every leg and keep the ordered path an avatar should face along."""
    if lidar_mesh is not None and collision_index is not None:
        raise ValueError("pass lidar_mesh or collision_index, not both")
    mesh_index = collision_index or (
        MeshCollisionIndex(lidar_mesh) if lidar_mesh is not None else None
    )
    legs: list[WorkflowLeg] = []
    stops = workflow.scenario.stops
    for index in range(len(stops) - 1):
        measured = measure.route_clear_width(graph, workflow.scenario, index)
        floor_plan_collision = (
            not measured.reachable or measured.inches < profile.body_width_inches
        )
        mesh_collision = bool(
            mesh_index
            and mesh_index.collides(measured.path, profile.body_width_inches / 2)
        )
        meets_clearance = (
            measured.reachable and measured.inches >= profile.required_width_inches
        )
        legs.append(
            WorkflowLeg(
                origin=stops[index].name,
                destination=stops[index + 1].name,
                reachable=measured.reachable,
                measured_width_inches=measured.inches,
                required_width_inches=profile.required_width_inches,
                meets_clearance=meets_clearance,
                floor_plan_collision=floor_plan_collision,
                mesh_collision=mesh_collision,
                collision_free=not floor_plan_collision and not mesh_collision,
                blocking_node_ids=tuple(measured.blocking_node_ids),
                route=_route_poses(measured.path),
            )
        )
    interactions = tuple(
        _evaluate_interaction(graph, workflow, interaction, profile, legs)
        for interaction in workflow.interactions
    )
    return WorkflowEvaluation(
        workflow=workflow,
        profile=profile,
        legs=tuple(legs),
        interactions=interactions,
    )


def workflow_candidate_rejection(
    before: SceneGraph,
    candidate: SceneGraph,
    *,
    workflows: list[Workflow],
    profiles: list[FunctionalProfile],
    measure: MeasurementProvider,
    lidar_mesh: LidarMesh | None = None,
    collision_index: MeshCollisionIndex | None = None,
) -> str | None:
    """Reject a layout that breaks access a prior layout already provided."""
    if lidar_mesh is not None and collision_index is not None:
        raise ValueError("pass lidar_mesh or collision_index, not both")
    mesh_index = collision_index or (
        MeshCollisionIndex(lidar_mesh) if lidar_mesh is not None else None
    )
    for workflow in workflows:
        for profile in profiles:
            prior = evaluate_workflow(
                before, workflow, profile, measure, collision_index=mesh_index
            )
            after = evaluate_workflow(
                candidate, workflow, profile, measure, collision_index=mesh_index
            )
            lost_leg = any(old.passed and not new.passed for old, new in zip(prior.legs, after.legs))
            lost_interaction = any(
                (old.reachable is True and new.reachable is not True)
                or (not old.needs_measurement and new.needs_measurement)
                for old, new in zip(prior.interactions, after.interactions)
            )
            if lost_leg or lost_interaction:
                return f"workflow_regression:{workflow.id}:{profile.id}"
    return None


def _evaluate_interaction(
    graph: SceneGraph,
    workflow: Workflow,
    interaction: Interaction,
    profile: FunctionalProfile,
    legs: list[WorkflowLeg],
) -> InteractionEvaluation:
    if interaction.target_node_id is None:
        return _unknown_interaction(interaction, profile)
    try:
        target = graph.by_id(interaction.target_node_id)
    except KeyError:
        return _unknown_interaction(interaction, profile)

    anchor_ids = {target.id, target.parent_id} - {None}
    stop_index = next(
        (
            index
            for index, stop in enumerate(workflow.scenario.stops)
            if stop.anchor_node_id in anchor_ids
        ),
        None,
    )
    if stop_index is None:
        return _unknown_interaction(interaction, profile)
    approach_clear = stop_index == 0 or legs[stop_index - 1].collision_free
    center = target.transform.position.z
    height_meters = center + target.dimensions.z / 2 if interaction.height == "top" else center
    measured_height = to_inches(height_meters)
    height_reachable = (
        None
        if profile.maximum_reach_inches is None
        else measured_height <= profile.maximum_reach_inches
    )
    reachable = approach_clear and height_reachable is not False
    return InteractionEvaluation(
        interaction=interaction,
        needs_measurement=False,
        approach_collision_free=approach_clear,
        measured_height_inches=measured_height,
        maximum_reach_inches=profile.maximum_reach_inches,
        height_reachable=height_reachable,
        reachable=reachable,
    )


def _unknown_interaction(
    interaction: Interaction, profile: FunctionalProfile
) -> InteractionEvaluation:
    return InteractionEvaluation(
        interaction=interaction,
        needs_measurement=True,
        approach_collision_free=None,
        measured_height_inches=None,
        maximum_reach_inches=profile.maximum_reach_inches,
        height_reachable=None,
        reachable=None,
    )




def _route_poses(points: list[Vec3]) -> tuple[RoutePose, ...]:
    """Face each avatar toward the next point, never backward along the route."""
    import math

    if not points:
        return ()
    poses: list[RoutePose] = []
    last_heading = 0.0
    for index, point in enumerate(points):
        if index + 1 < len(points):
            following = points[index + 1]
            dx, dy = following.x - point.x, following.y - point.y
            if dx or dy:
                last_heading = math.degrees(math.atan2(dy, dx))
        poses.append(RoutePose(position=point, heading_degrees=last_heading))
    return tuple(poses)
