"""Workflows, functional profiles and the builders that turn a scanned room into task journeys."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from standardphysics_contracts import (
    Scenario,
    SceneGraph,
    SceneNode,
    Stop,
    Vec3,
)

from .checks.walls import is_room_shell


@dataclass(frozen=True)
class FunctionalProfile:
    id: str
    title: str
    avatar: str
    body_width_inches: float
    required_width_inches: float
    turning_diameter_inches: float
    maximum_reach_inches: float | None = None
    compliance_profile: bool = False


WHEELCHAIR_PROFILE = FunctionalProfile(
    id="wheelchair",
    title="Wheelchair user",
    avatar="wheelchair",
    body_width_inches=30.0,
    required_width_inches=36.0,
    turning_diameter_inches=60.0,
    maximum_reach_inches=48.0,
    compliance_profile=True,
)

MOBILITY_AID_PROFILE = FunctionalProfile(
    id="mobility-aid",
    title="Mobility aid user",
    avatar="mobility-aid",
    body_width_inches=24.0,
    required_width_inches=36.0,
    turning_diameter_inches=60.0,
    maximum_reach_inches=48.0,
)

LOW_REACH_PROFILE = FunctionalProfile(
    id="lower-reach",
    title="Short stature or lower reach",
    avatar="short-stature",
    body_width_inches=18.0,
    required_width_inches=36.0,
    turning_diameter_inches=60.0,
    maximum_reach_inches=36.0,
)

LARGER_BODY_PROFILE = FunctionalProfile(
    id="larger-body",
    title="Larger body",
    avatar="larger-body",
    body_width_inches=36.0,
    required_width_inches=36.0,
    turning_diameter_inches=60.0,
    maximum_reach_inches=48.0,
)

DEFAULT_PROFILES = (
    WHEELCHAIR_PROFILE,
    MOBILITY_AID_PROFILE,
    LOW_REACH_PROFILE,
    LARGER_BODY_PROFILE,
)

@dataclass(frozen=True)
class Workflow:
    id: str
    title: str
    scenario: Scenario
    interactions: tuple[Interaction, ...] = ()


@dataclass(frozen=True)
class Interaction:
    """Something a person must reach after arriving at its anchored stop."""

    id: str
    title: str
    target_node_id: UUID | None
    height: Literal["center", "top"]


@dataclass(frozen=True)
class RoutePose:
    position: Vec3
    heading_degrees: float


def build_workflow_suite(
    graph: SceneGraph,
    scenario: Scenario,
    *,
    interactions: list[Interaction] | tuple[Interaction, ...] = (),
) -> list[Workflow]:
    """Expand known stops and task targets into broad scan-space coverage."""
    workflows = [
        Workflow(
            id="journey:full",
            title=scenario.name,
            scenario=scenario,
            interactions=tuple(interactions),
        )
    ]
    for origin_index, origin in enumerate(scenario.stops):
        for destination_index, destination in enumerate(scenario.stops):
            if origin_index == destination_index:
                continue
            workflows.append(
                Workflow(
                    id=f"route:{origin_index}:{destination_index}",
                    title=f"{origin.name} to {destination.name}",
                    scenario=Scenario(
                        name=f"{origin.name} to {destination.name}",
                        stops=[origin, destination],
                    ),
                )
            )
    for interaction in interactions:
        workflows.extend(_interaction_workflows(graph, scenario, interaction))
    return workflows


def build_entrance_object_workflows(
    graph: SceneGraph, scenario: Scenario
) -> list[Workflow]:
    """Route every inferred or Astra-labelled entrance to every scanned object."""
    by_id = {node.id: node for node in graph.nodes}
    entrance_terms = {"door", "entrance", "entry", "exit", "opening"}
    scenario_entrances = [
        stop
        for stop in scenario.stops
        if (
            stop.anchor_node_id in by_id
            and by_id[stop.anchor_node_id].kind in {"door", "opening"}
        )
        or entrance_terms & set(stop.name.casefold().replace("-", " ").split())
    ]
    entrances: list[Stop] = []
    anchored_entrances: set[UUID] = set()
    unanchored_positions: set[tuple[float, float, float]] = set()

    for stop in scenario_entrances:
        if stop.anchor_node_id is not None:
            if stop.anchor_node_id in anchored_entrances:
                continue
            anchored_entrances.add(stop.anchor_node_id)
        else:
            position = stop.position.as_tuple()
            if position in unanchored_positions:
                continue
            unanchored_positions.add(position)
        entrances.append(stop)

    for node in graph.nodes:
        if node.kind not in {"door", "opening"} or node.id in anchored_entrances:
            continue
        anchored_entrances.add(node.id)
        entrances.append(_stop_at_node(node))

    existing_stops = {
        stop.anchor_node_id: stop
        for stop in scenario.stops
        if stop.anchor_node_id is not None
    }
    workflows = []
    for entrance_index, entrance in enumerate(entrances):
        for target in (
            node for node in graph.contents() if not is_room_shell(node)
        ):
            destination = existing_stops.get(target.id) or _stop_at_node(target)
            workflows.append(
                Workflow(
                    id=f"entrance-object:{entrance_index}:{target.id}",
                    title=f"{entrance.name} to {target.label}",
                    scenario=Scenario(
                        name=f"{entrance.name} to {target.label}",
                        stops=[entrance, destination],
                    ),
                )
            )
    return workflows


def _stop_at_node(node: SceneNode) -> Stop:
    position = node.transform.position
    return Stop(
        name=node.label,
        position=Vec3(x=position.x, y=position.y, z=0.0),
        anchor_node_id=node.id,
    )


def _interaction_workflows(
    graph: SceneGraph,
    scenario: Scenario,
    interaction: Interaction,
) -> list[Workflow]:
    if interaction.target_node_id is None:
        return [
            Workflow(
                id=f"task:{interaction.id}:needs-measurement",
                title=interaction.title,
                scenario=scenario,
                interactions=(interaction,),
            )
        ]
    try:
        target = graph.by_id(interaction.target_node_id)
    except KeyError:
        return [
            Workflow(
                id=f"task:{interaction.id}:needs-measurement",
                title=interaction.title,
                scenario=scenario,
                interactions=(interaction,),
            )
        ]
    anchor_ids = {target.id, target.parent_id} - {None}
    existing_destination = next(
        (stop for stop in scenario.stops if stop.anchor_node_id in anchor_ids),
        None,
    )
    if existing_destination is None:
        position = target.transform.position
        destination = Stop(
            name=interaction.title,
            position=Vec3(x=position.x, y=position.y, z=0.0),
            anchor_node_id=target.id,
        )
    else:
        destination = existing_destination.model_copy(update={"name": interaction.title})
    return [
        Workflow(
            id=f"task:{interaction.id}:{origin_index}",
            title=f"{origin.name} to {interaction.title}",
            scenario=Scenario(
                name=f"{origin.name} to {interaction.title}",
                stops=[origin, destination],
            ),
            interactions=(interaction,),
        )
        for origin_index, origin in enumerate(scenario.stops)
        if origin.anchor_node_id not in anchor_ids
    ]

