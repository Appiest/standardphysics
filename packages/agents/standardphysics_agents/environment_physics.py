"""One-inch mobility physics and environment-wide route screening.

The calculations are deterministic. LiDAR surface normals estimate slopes and
vertical faces identify possible risers; a simple rigid-body wheelchair model
screens uncontrolled rolling and tipping. These are safety signals, not legal
or biomechanical conclusions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal
from uuid import UUID

import numpy as np
from standardphysics_contracts import (
    EnvironmentPhysicsResult,
    LidarMesh,
    MeasurementProvider,
    PhysicsObservation,
    PhysicsRoute,
    Scenario,
    SceneGraph,
    SceneNode,
    Stop,
    Vec3,
    to_inches,
    to_meters,
)

from .evaluation.scan_space import world_triangles

ONE_INCH_METERS = to_meters(1.0)
GRAVITY_METERS_PER_SECOND_SQUARED = 9.80665
MAX_ROUTE_PAIRS = 5_000
MAX_LEVEL_CHANGE_OBSERVATIONS = 50

EXIT_TERMS = frozenset({"door", "exit", "entrance", "entry", "opening", "way out"})
SEAT_TERMS = frozenset({"chair", "seat", "stool", "bench", "waiting"})
CASHIER_TERMS = frozenset(
    {"cashier", "register", "checkout", "counter", "point of sale", "service"}
)
STAIR_TERMS = frozenset({"stair", "stairs", "step", "steps", "curb", "threshold"})
RAMP_TERMS = frozenset({"ramp", "slope", "incline"})
CUSTOMER_ROUTE_KINDS = frozenset({"door", "opening", "object"})


@dataclass(frozen=True)
class WheelchairPhysicsProfile:
    """Explicit simulation assumptions, separate from accessibility rules."""

    rolling_resistance_coefficient: float = 0.02
    center_of_mass_height_meters: float = 0.55
    wheelbase_meters: float = 0.65
    track_width_meters: float = 0.60
    wheel_climb_limit_inches: float = 0.5
    turning_diameter_inches: float = 60.0

    def __post_init__(self) -> None:
        values = (
            self.rolling_resistance_coefficient,
            self.center_of_mass_height_meters,
            self.wheelbase_meters,
            self.track_width_meters,
            self.wheel_climb_limit_inches,
            self.turning_diameter_inches,
        )
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("wheelchair physics assumptions must be finite and positive")


DEFAULT_WHEELCHAIR_PHYSICS_PROFILE = WheelchairPhysicsProfile()


def analyze_environment_physics(
    graph: SceneGraph,
    scenario: Scenario,
    measure: MeasurementProvider,
    *,
    mesh: LidarMesh | None = None,
    profile: WheelchairPhysicsProfile = DEFAULT_WHEELCHAIR_PHYSICS_PROFILE,
) -> EnvironmentPhysicsResult:
    observations = _named_level_changes(graph)
    triangles_checked = 0
    surface_samples = 0
    mesh_limitation: str | None = None
    if mesh is not None and mesh.floorY is not None:
        triangles = world_triangles(mesh)
        triangles_checked = len(triangles)
        surface = _surface_physics(triangles, profile)
        observations.extend(surface.observations)
        surface_samples = surface.samples
    elif mesh is not None:
        mesh_limitation = (
            "The LiDAR mesh has no floor reference, so slope and riser physics "
            "were not evaluated. Rescan with floor alignment."
        )

    routes, counts, route_observations = _environment_routes(
        graph, scenario, measure, profile
    )
    observations.extend(route_observations)
    limitations = [
        "Results screen mobility and physical risk; they do not certify ADA or fire-code compliance.",
        "Slope, roll, and tip checks use LiDAR triangle normals and a simplified rigid wheelchair model.",
        "Possible risers from LiDAR require a rescan or tape measurement before design work.",
        "Evacuation routes cover measured distance, clearance, and turning only; smoke, occupant load, signage, lighting, door force, and emergency operations need professional review.",
        "One-inch resolution is finer than many scans' practical accuracy; uncertainty remains evidence, not a pass.",
    ]
    if mesh is None:
        limitations.append("No LiDAR mesh was supplied, so slope and riser physics were not evaluated.")
    elif mesh_limitation is not None:
        limitations.append(mesh_limitation)
    if not counts["exits"]:
        limitations.append("No exit or entrance candidate was identified in the scan labels.")
    if not counts["cashiers"]:
        limitations.append("No cashier, register, or service-counter candidate was identified.")
    return EnvironmentPhysicsResult(
        resolution_inches=1.0,
        mesh_triangles_checked=triangles_checked,
        surface_samples=surface_samples,
        observations=observations,
        routes=routes,
        exits_found=counts["exits"],
        seats_found=counts["seats"],
        cashiers_found=counts["cashiers"],
        limitations=limitations,
    )


@dataclass(frozen=True)
class _SurfaceResult:
    observations: list[PhysicsObservation]
    samples: int


def _surface_physics(
    triangles: np.ndarray, profile: WheelchairPhysicsProfile
) -> _SurfaceResult:
    if not len(triangles):
        return _SurfaceResult([], 0)
    left = triangles[:, 1] - triangles[:, 0]
    right = triangles[:, 2] - triangles[:, 0]
    normals = np.cross(left, right)
    lengths = np.linalg.norm(normals, axis=1)
    valid = lengths > 1e-10
    unit = np.zeros_like(normals)
    unit[valid] = normals[valid] / lengths[valid, None]
    vertical_component = np.abs(unit[:, 2])
    slope_degrees = np.degrees(
        np.arctan2(np.linalg.norm(unit[:, :2], axis=1), vertical_component)
    )
    centroids = triangles.mean(axis=1)
    floor_band = (
        valid
        & (slope_degrees <= 45.0)
        & (centroids[:, 2] >= -ONE_INCH_METERS)
        & (centroids[:, 2] <= 1.2)
    )
    indices = np.flatnonzero(floor_band)
    observations: list[PhysicsObservation] = []
    if len(indices):
        steepest_index = int(indices[np.argmax(slope_degrees[indices])])
        angle = float(slope_degrees[steepest_index])
        point = _point(centroids[steepest_index])
        observations.append(
            PhysicsObservation(
                kind="surface_slope",
                status="potential_barrier" if angle > 0.5 else "clear",
                title="Steepest walkable-surface triangle in the captured mesh",
                measured_value=angle,
                unit="degrees",
                source="lidar_mesh",
                point=point,
            )
        )
        acceleration = _roll_acceleration(angle, profile)
        observations.append(
            PhysicsObservation(
                kind="uncontrolled_roll",
                status="potential_barrier" if acceleration > 0 else "clear",
                title="Simplified unbraked downhill rolling screen",
                measured_value=acceleration,
                reference_value=0.0,
                unit="m/s^2",
                source="lidar_mesh",
                point=point,
            )
        )
        tip_margin = _tip_margin_degrees(profile) - angle
        observations.append(
            PhysicsObservation(
                kind="wheelchair_tip",
                status="potential_barrier" if tip_margin < 0 else "clear",
                title="Simplified rigid-wheelchair downhill tip margin",
                measured_value=tip_margin,
                reference_value=0.0,
                unit="degrees margin",
                source="lidar_mesh",
                point=point,
            )
        )
    observations.extend(_riser_observations(triangles, unit, valid, profile))
    return _SurfaceResult(observations, len(indices))


def _roll_acceleration(
    slope_degrees: float, profile: WheelchairPhysicsProfile
) -> float:
    radians = math.radians(slope_degrees)
    return GRAVITY_METERS_PER_SECOND_SQUARED * max(
        math.sin(radians)
        - profile.rolling_resistance_coefficient * math.cos(radians),
        0.0,
    )


def _tip_margin_degrees(profile: WheelchairPhysicsProfile) -> float:
    base = min(profile.wheelbase_meters, profile.track_width_meters)
    return math.degrees(math.atan(base / (2 * profile.center_of_mass_height_meters)))


def _riser_observations(
    triangles: np.ndarray,
    unit_normals: np.ndarray,
    valid: np.ndarray,
    profile: WheelchairPhysicsProfile,
) -> list[PhysicsObservation]:
    rises = np.ptp(triangles[:, :, 2], axis=1)
    centroids = triangles.mean(axis=1)
    vertical_faces = (
        valid
        & (np.abs(unit_normals[:, 2]) < 0.25)
        & (rises >= to_meters(0.25))
        & (rises <= to_meters(12.0))
        & (centroids[:, 2] >= 0)
        & (centroids[:, 2] <= 1.2)
    )
    candidates = np.flatnonzero(vertical_faces)
    ordered = sorted(candidates, key=lambda index: float(rises[index]), reverse=True)
    observations = []
    seen: set[tuple[int, int, int]] = set()
    for index in ordered:
        point = centroids[index]
        key = tuple(np.rint(point / ONE_INCH_METERS).astype(int))
        if key in seen:
            continue
        seen.add(key)
        rise_inches = to_inches(float(rises[index]))
        observations.append(
            PhysicsObservation(
                kind="level_change",
                status=(
                    "potential_barrier"
                    if rise_inches > profile.wheel_climb_limit_inches
                    else "needs_measurement"
                ),
                title="LiDAR suggests a curb, threshold, step, or riser",
                measured_value=rise_inches,
                reference_value=profile.wheel_climb_limit_inches,
                unit="in",
                source="lidar_mesh",
                point=_point(point),
            )
        )
        if len(observations) >= MAX_LEVEL_CHANGE_OBSERVATIONS:
            break
    return observations


def _named_level_changes(graph: SceneGraph) -> list[PhysicsObservation]:
    observations = []
    for node in graph.nodes:
        words = _words(node)
        if words & STAIR_TERMS:
            observations.append(
                PhysicsObservation(
                    kind="stair_or_step",
                    status="potential_barrier",
                    title=f"{node.label} needs riser, tread, and alternate-route review",
                    measured_value=to_inches(node.dimensions.z),
                    unit="in total captured height",
                    source="scene_graph",
                    point=node.transform.position,
                    node_ids=[node.id],
                )
            )
        elif words & RAMP_TERMS:
            observations.append(
                PhysicsObservation(
                    kind="surface_slope",
                    status="needs_measurement",
                    title=f"{node.label} needs rise, run, cross-slope, and landing measurements",
                    source="scene_graph",
                    point=node.transform.position,
                    node_ids=[node.id],
                )
            )
    return observations


def _environment_routes(
    graph: SceneGraph,
    scenario: Scenario,
    measure: MeasurementProvider,
    profile: WheelchairPhysicsProfile,
) -> tuple[list[PhysicsRoute], dict[str, int], list[PhysicsObservation]]:
    exits = _nodes(graph, EXIT_TERMS, kinds={"door", "opening"})
    seats = _nodes(graph, SEAT_TERMS, kinds={"object"})
    cashiers = _nodes(graph, CASHIER_TERMS, kinds={"object"})
    exits = _with_scenario_anchors(graph, scenario, exits, {"exit", "entrance", "way out"})
    cashiers = _with_scenario_anchors(graph, scenario, cashiers, {"cashier", "counter", "register"})
    targets = _customer_route_nodes(graph, scenario)
    exit_ids = {node.id for node in exits}
    seat_ids = {node.id for node in seats}
    cashier_ids = {node.id for node in cashiers}
    route_pair_count = len(targets) * (len(targets) - 1)
    if route_pair_count > MAX_ROUTE_PAIRS:
        raise ValueError(f"environment route pair count exceeds {MAX_ROUTE_PAIRS}")

    pairs = (
        (
            _route_purpose(
                origin.id,
                destination.id,
                exit_ids=exit_ids,
                seat_ids=seat_ids,
                cashier_ids=cashier_ids,
            ),
            origin,
            destination,
        )
        for origin in targets
        for destination in targets
        if origin.id != destination.id
    )

    routes = []
    turning_observations = []
    for purpose, origin, destination in pairs:
        pair_scenario = Scenario(
            name=f"{origin.label} to {destination.label}",
            stops=[_stop(origin), _stop(destination)],
        )
        width = measure.route_clear_width(graph, pair_scenario, 0)
        route_points = width.path or [
            pair_scenario.stops[0].position,
            pair_scenario.stops[1].position,
        ]
        distance = _path_length_inches(route_points) if width.reachable else None
        routes.append(
            PhysicsRoute(
                purpose=purpose,
                origin_node_id=origin.id,
                destination_node_id=destination.id,
                reachable=width.reachable,
                distance_inches=distance,
                clear_width_inches=width.inches,
                blocking_node_ids=width.blocking_node_ids,
            )
        )
        turn = measure.turning_space(graph, pair_scenario.stops[-1].position)
        available_turn = min(turn.inches_wide, turn.inches_deep)
        if not turn.fits or available_turn < profile.turning_diameter_inches:
            turning_observations.append(
                PhysicsObservation(
                    kind="turning",
                    status="potential_barrier",
                    title=f"Wheelchair turning screen on {pair_scenario.name}",
                    measured_value=available_turn,
                    reference_value=profile.turning_diameter_inches,
                    unit="in",
                    source="route_geometry",
                    point=turn.center,
                )
            )
    counts = {"exits": len(exits), "seats": len(seats), "cashiers": len(cashiers)}
    return routes, counts, turning_observations[:MAX_LEVEL_CHANGE_OBSERVATIONS]


def _route_purpose(
    origin_id: UUID,
    destination_id: UUID,
    *,
    exit_ids: set[UUID],
    seat_ids: set[UUID],
    cashier_ids: set[UUID],
) -> Literal["customer_access", "evacuation", "seat_to_cashier"]:
    if destination_id in exit_ids and (
        origin_id in seat_ids or origin_id in cashier_ids
    ):
        return "evacuation"
    if origin_id in seat_ids and destination_id in cashier_ids:
        return "seat_to_cashier"
    return "customer_access"


def _customer_route_nodes(
    graph: SceneGraph, scenario: Scenario
) -> list[SceneNode]:
    """Every scanned destination plus anything the owner explicitly anchored."""
    anchored_ids = {
        stop.anchor_node_id
        for stop in scenario.stops
        if stop.anchor_node_id is not None
    }
    return [
        node
        for node in graph.nodes
        if node.kind in CUSTOMER_ROUTE_KINDS or node.id in anchored_ids
    ]


def _nodes(graph: SceneGraph, terms: frozenset[str], *, kinds: set[str]) -> list[SceneNode]:
    return [node for node in graph.nodes if node.kind in kinds and _words(node) & terms]


def _with_scenario_anchors(
    graph: SceneGraph,
    scenario: Scenario,
    nodes: list[SceneNode],
    terms: set[str],
) -> list[SceneNode]:
    by_id = {node.id: node for node in graph.nodes}
    found = {node.id: node for node in nodes}
    for stop in scenario.stops:
        if stop.anchor_node_id in by_id and _tokens(stop.name) & terms:
            found[stop.anchor_node_id] = by_id[stop.anchor_node_id]
    return list(found.values())


def _stop(node: SceneNode) -> Stop:
    point = node.transform.position
    return Stop(
        name=node.label,
        position=Vec3(x=point.x, y=point.y, z=0.0),
        anchor_node_id=node.id,
    )


def _path_length_inches(path: list[Vec3]) -> float:
    meters = sum(
        math.dist((left.x, left.y), (right.x, right.y))
        for left, right in pairwise(path)
    )
    return to_inches(meters)


def _words(node: SceneNode) -> set[str]:
    return _tokens(f"{node.label} {node.raw_category}")


def _tokens(text: str) -> set[str]:
    lowered = text.casefold().replace("_", " ").replace("-", " ")
    words = lowered.split()
    tokens = set(words)
    tokens.update(" ".join(words[index : index + size])
                  for size in (2, 3)
                  for index in range(len(words) - size + 1))
    return tokens


def _point(values: np.ndarray) -> Vec3:
    return Vec3(x=float(values[0]), y=float(values[1]), z=float(values[2]))


__all__ = [
    "ONE_INCH_METERS",
    "WheelchairPhysicsProfile",
    "analyze_environment_physics",
]
