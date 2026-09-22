"""A measured approach to a target, for deciding what furniture could improve it.

The approach is one journey by one occupant body: from a confirmed start, along
the same route and swept-collision machinery the assessment already uses, to a
standing point beside the target, then the turn to face it and the personal
reach to it. Everything missing stays missing: a blocked path, an unknown
floor, an unmeasured height or a hidden reach is reported, never argued away.

Outcomes are `clear`, `blocked` and `needs_verification`. `clear` requires
every measured input to be present and to pass for every occupant profile
asked about. There is no code path that turns a missing measurement into a
passing approach by dropping a constraint.

Thin wall attachments are not solid obstacles: the surface that carries the
target (the wall or bench the outlet sits in) is exempt from the direct
obstruction line between the standing point and the target, and from nothing
else. Furniture standing between occupant and target still blocks.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Literal

import numpy as np
from standardphysics_contracts import (
    Scenario,
    SceneGraph,
    SceneNode,
    Stop,
    Vec3,
    bounds_the_room,
    lies_flat,
    to_inches,
    to_meters,
)
from standardphysics_pipeline import (
    build_grid,
    clearance_map,
    contains_point,
    footprint,
    gap_between,
)
from standardphysics_pipeline.occupancy import blocks_floor

from ..mesh_collision import MeshCollisionIndex
from .occupancy import (
    DEFAULT_OCCUPANTS,
    OccupantProfile,
    ensure_spacing,
)

ApproachStatus = Literal["clear", "blocked", "needs_verification"]

HorizontalStatus = Literal["at_target", "beyond_body_envelope", "unmeasured"]

OBSTRUCTION_STEP_METERS = 0.05
"""How finely the occupant-to-target line is sampled for furniture in the way."""

GROUND_FLOOR_TOLERANCE_METERS = to_meters(12.0)
"""How far a flat sheet may sit from the plan origin and still be the floor
under the route. A ceiling over the same footprint lies flat too, and is not
walkable evidence."""


@dataclass(frozen=True)
class ReachRecord:
    """Personal reach for one occupant; legal reach is decided in the assessment.

    Height is the measured top of the target against the occupant's assumed
    grasp ceiling. The horizontal distance from the standing point to the
    target is screened against the occupant's own body envelope (half the
    footprint diagonal): the body is measured, the arm is not, so nothing
    beyond the body envelope is ever claimed reachable.
    """

    occupant_id: str
    occupant_title: str
    target_height_inches: float | None
    personal_reach_inches: float | None
    status: Literal["within_personal_reach", "beyond_personal_reach", "unmeasured"]
    horizontal_distance_inches: float | None
    horizontal_status: HorizontalStatus
    body_envelope_radius_inches: float

    @property
    def measured(self) -> bool:
        return self.status != "unmeasured" and self.horizontal_status != "unmeasured"


@dataclass(frozen=True)
class ApproachResult:
    """Everything measured about one occupant journey to one target.

    `clear` means the measured approach holds for every occupant asked about:
    path, aisle, turn, mesh sweep, standing floor, a target beside the body
    and within personal reach. It never means legally compliant, and every
    unmeasured input keeps the status at `needs_verification`.
    """

    target_id: uuid.UUID
    status: ApproachStatus
    reasons: tuple[str, ...]
    """Blockers first; then the measurements still missing when the approach
    is otherwise sound (`needs_verification`)."""

    approach_stop: Vec3 | None
    path: tuple[Vec3, ...] | None
    aisle_width_inches: float | None
    turning_space_inches: float | None
    obstruction_labels: tuple[str, ...]
    floor_supported: bool | None
    mesh_checked: bool
    mesh_collision: bool
    reaches: tuple[ReachRecord, ...]
    unverified: tuple[str, ...]
    """The remainder that stays unknown even when the status is clear."""


def target_height_inches(node: SceneNode) -> float | None:
    """Top of the target above the floor, or None when the scan did not measure it.

    A target with no height dimension or a zero height is unmeasured, not
    zero inches tall.
    """
    height = node.dimensions.z
    top = node.transform.position.z + height / 2
    if not math.isfinite(height) or height <= 0 or not math.isfinite(top) or top < 0:
        return None
    return to_inches(top)


def support_of(graph: SceneGraph, target: SceneNode) -> frozenset[uuid.UUID]:
    """The surfaces the target is attached to, and nothing else.

    Only room structure (things that bound the space, like the wall a socket
    hangs in) whose footprint contains the target's plan position counts as a
    support, plus the target's own parent chain. Furniture is never inferred
    to be the mounting surface from plan coincidence alone: a sofa pushed
    over an outlet covers it, and exempting everything that merely covers the
    point would let it hide the block.
    """
    by_id = {node.id: node for node in graph.nodes}
    position = (target.transform.position.x, target.transform.position.y)
    supports = {
        other.id
        for other in graph.nodes
        if other.id != target.id
        and not lies_flat(other)
        and bounds_the_room(other)
        and contains_point(footprint(other), position)
    }
    attached: set[uuid.UUID] = set()
    current = target
    while current.parent_id is not None and current.parent_id in by_id:
        parent = by_id[current.parent_id]
        if parent.id in attached:
            break
        attached.add(parent.id)
        current = parent
    return frozenset(supports | attached | {target.id})


def _widest(occupants: tuple[OccupantProfile, ...]) -> OccupantProfile:
    return max(occupants, key=lambda profile: profile.travel_width_inches)


def suggestion_stop(
    graph: SceneGraph, target: SceneNode, occupants: tuple[OccupantProfile, ...]
) -> Vec3 | None:
    """A standing point beside the target where every occupant's body fits.

    Picked from the same occupancy grid the route uses: the closest indoor
    cell to the target whose clear disc covers the widest occupant entirely.
    None when no scanned floor cell gives the occupant room to stand off the
    target at all, which the caller reports as blocked rather than guessing.
    """
    grid = build_grid(graph)
    if grid.indoors is None:
        return None
    clearance = clearance_map(grid)
    widest = _widest(occupants)
    radius_meters = to_meters(widest.travel_width_inches) / 2
    rows, cols = grid.occupied.shape
    world_x = grid.origin_x + (np.arange(cols) + 0.5) * grid.cell_size
    world_y = grid.origin_y + (np.arange(rows) + 0.5) * grid.cell_size
    world_x, world_y = np.meshgrid(world_x, world_y)
    candidate = (
        grid.indoors
        & ~grid.occupied
        & (clearance >= radius_meters)
    )
    if not candidate.any():
        return None
    target_position = np.asarray(
        [target.transform.position.x, target.transform.position.y]
    )
    distance = np.hypot(
        world_x - target_position[0], world_y - target_position[1]
    )
    masked = np.where(candidate, distance, np.inf)
    row, col = np.unravel_index(int(np.argmin(masked)), masked.shape)
    if not np.isfinite(masked[row, col]):
        return None
    return Vec3(x=float(world_x[row, col]), y=float(world_y[row, col]), z=0.0)


def _line_obstructions(
    graph: SceneGraph,
    target: SceneNode,
    stop: Vec3,
    support: frozenset[uuid.UUID],
) -> tuple[str, ...]:
    """Furniture that stands in the straight line between occupant and target.

    The support surface itself is exempt; a sofa pushed in front of the outlet
    is not. Sampling the line every five centimetres is plenty to catch any
    piece wide enough to matter.
    """
    start = (stop.x, stop.y)
    end = (target.transform.position.x, target.transform.position.y)
    distance = math.dist(start, end)
    steps = max(int(distance // OBSTRUCTION_STEP_METERS), 1)
    obstacles = [
        node
        for node in graph.nodes
        if node.id not in support
        and not bounds_the_room(node)
        and blocks_floor(node)
    ]
    found: set[str] = set()
    for step in range(steps + 1):
        fraction = step / steps
        point = (
            start[0] + (end[0] - start[0]) * fraction,
            start[1] + (end[1] - start[1]) * fraction,
        )
        for node in obstacles:
            if contains_point(footprint(node), point):
                found.add(node.label)
    return tuple(sorted(found))


def _floor_evidence(graph: SceneGraph) -> bool:
    """Whether the scan carries a ground-plane sheet the route could stand on.

    `lies_flat` alone is not floor evidence: a ceiling over the same
    footprint lies flat too. The sheet must also sit within a foot of the
    plan origin to count as the floor under this route.
    """
    return any(
        lies_flat(node)
        and abs(node.transform.position.z) <= GROUND_FLOOR_TOLERANCE_METERS
        for node in graph.nodes
    )


def _plan_gap_inches(stop: Vec3, target: SceneNode) -> float:
    """Plan distance from the standing point to the target's footprint.

    Measured to the footprint rather than the centre, so a wide built-in
    surface is not credited as being further than its facing edge. Returns
    zero when the point stands inside the footprint.
    """
    speck = 1e-6
    probe = [
        (stop.x - speck, stop.y - speck),
        (stop.x + speck, stop.y - speck),
        (stop.x + speck, stop.y + speck),
        (stop.x - speck, stop.y + speck),
    ]
    return to_inches(gap_between(footprint(target), probe))


def _reach_record(
    target: SceneNode, profile: OccupantProfile, stop: Vec3 | None
) -> ReachRecord:
    horizontal: HorizontalStatus = "unmeasured"
    distance: float | None = None
    if stop is not None:
        distance = _plan_gap_inches(stop, target)
        horizontal = (
            "at_target"
            if distance <= profile.envelope_radius_inches
            else "beyond_body_envelope"
        )
    height = target_height_inches(target)
    if height is None:
        return ReachRecord(
            occupant_id=profile.id,
            occupant_title=profile.title,
            target_height_inches=None,
            personal_reach_inches=profile.personal_reach_inches,
            status="unmeasured",
            horizontal_distance_inches=distance,
            horizontal_status=horizontal,
            body_envelope_radius_inches=profile.envelope_radius_inches,
        )
    if profile.personal_reach_inches is None:
        return ReachRecord(
            occupant_id=profile.id,
            occupant_title=profile.title,
            target_height_inches=height,
            personal_reach_inches=None,
            status="unmeasured",
            horizontal_distance_inches=distance,
            horizontal_status=horizontal,
            body_envelope_radius_inches=profile.envelope_radius_inches,
        )
    status = (
        "within_personal_reach"
        if height <= profile.personal_reach_inches
        else "beyond_personal_reach"
    )
    return ReachRecord(
        occupant_id=profile.id,
        occupant_title=profile.title,
        target_height_inches=height,
        personal_reach_inches=profile.personal_reach_inches,
        status=status,
        horizontal_distance_inches=distance,
        horizontal_status=horizontal,
        body_envelope_radius_inches=profile.envelope_radius_inches,
    )


def _arrival_turning(
    graph: SceneGraph,
    measure,
    path: list[Vec3],
    stop: Vec3,
    profile: OccupantProfile,
) -> tuple[float, Vec3]:
    """The widest turning circle on the final approach stretch.

    A circle somewhere on the whole route — a lobby by the front door — does
    not prove the occupant can turn where the arrival meets the target. The
    screen walks the measured path backwards from the stop for one turning
    diameter, and around each point samples lateral freedom up to the body
    envelope, so a wall-hugging arrival still finds the circle a body's width
    to the side when one exists, and a dead-end aisle finds none.
    """
    diameter = to_meters(profile.turning_diameter_inches)
    envelope = profile.envelope_radius_meters
    samples: list[Vec3] = [stop]
    walked = 0.0
    paired = list(zip(path[::-1], path[-2::-1]))
    for direction_from, direction_to in paired:
        walked += math.dist(
            (direction_from.x, direction_from.y), (direction_to.x, direction_to.y)
        )
        if walked > diameter:
            break
        delta = (
            direction_from.x - direction_to.x,
            direction_from.y - direction_to.y,
        )
        length = math.hypot(*delta)
        if length < 1e-9:
            samples.append(direction_from)
            continue
        perpendicular = (-delta[1] / length, delta[0] / length)
        for fraction in (0.5, 1.0):
            offset = envelope * fraction
            samples.append(
                Vec3(
                    x=direction_from.x + perpendicular[0] * offset,
                    y=direction_from.y + perpendicular[1] * offset,
                    z=0.0,
                )
            )
            samples.append(
                Vec3(
                    x=direction_from.x - perpendicular[0] * offset,
                    y=direction_from.y - perpendicular[1] * offset,
                    z=0.0,
                )
            )
    best_inches = 0.0
    best_point: Vec3 = stop
    for sample in samples:
        turn = measure.turning_space(graph, sample)
        available = min(turn.inches_wide, turn.inches_deep)
        if available > best_inches:
            best_inches, best_point = available, sample
    return best_inches, best_point


def _turning_blockers(
    graph: SceneGraph,
    measure,
    stop: Vec3,
    path: list[Vec3],
    occupants: tuple[OccupantProfile, ...],
) -> tuple[list[str], float | None]:
    """Which occupants cannot turn on the arrival, and the turn room found.

    The reported figure is the tightest turning measurement across all
    occupants' arrival stretches, so a caller can show the number behind the
    verdict.
    """
    blocked: list[str] = []
    available_inches: float | None = None
    for profile in occupants:
        best_inches = _arrival_turning(graph, measure, path, stop, profile)[0]
        if available_inches is None or best_inches < available_inches:
            available_inches = best_inches
        if best_inches < profile.turning_diameter_inches:
            blocked.append(
                f"no turning space on the arrival for {profile.title} "
                f"({best_inches:.1f} in < {profile.turning_diameter_inches:.1f} in)"
            )
    return blocked, available_inches


def evaluate_approach(
    graph: SceneGraph,
    target: SceneNode,
    confirmed_start: Stop,
    measure,
    occupants: tuple[OccupantProfile, ...] = DEFAULT_OCCUPANTS,
    mesh: MeshCollisionIndex | None = None,
    approach_stop: Vec3 | None = None,
) -> ApproachResult:
    """One measured journey from the confirmed start to the target.

    `confirmed_start` is the role-confirmed origin (a scenario stop the owner
    verified). Everything after it is a candidate, and every candidate step is
    checked: path presence, aisle width, turning room, standing floor, the
    swept body against raw capture, direct obstructions and personal reach. A
    missing measurement can downgrade a candidate to `needs_verification` but
    never promote it to `clear`.
    """
    if not occupants:
        raise ValueError("at least one occupant profile is required")

    support = support_of(graph, target)
    floor_ok = _floor_evidence(graph)

    stop = approach_stop
    if stop is None:
        stop = suggestion_stop(graph, target, occupants)

    reaches = tuple(_reach_record(target, profile, stop) for profile in occupants)

    blocked: list[str] = []
    unverified: list[str] = []
    path: tuple[Vec3, ...] | None = None
    width_inches: float | None = None
    turning: float | None = None
    obstructions: tuple[str, ...] = ()
    mesh_checked = False
    mesh_collision = False

    if stop is None:
        if floor_ok:
            blocked.append("no standing floor beside the target")
        else:
            unverified.append(
                "the floor is unobserved; no standing point could be derived"
            )
    else:
        scenario = Scenario(
            name=f"Approach {target.label}",
            stops=[
                Stop(
                    name="Confirmed start",
                    position=confirmed_start.position,
                    anchor_node_id=confirmed_start.anchor_node_id,
                ),
                Stop(name=f"At {target.label}", position=stop, anchor_node_id=target.id),
            ],
        )
        route = measure.route_clear_width(graph, scenario, 0)
        if not route.reachable:
            blocked.append("no cleared path from the confirmed start to the target")
            by_id = {node.id: node for node in graph.nodes}
            blockers = sorted(
                {by_id[node_id].label for node_id in route.blocking_node_ids if node_id in by_id}
            )
            if blockers:
                blocked.append("blocked by: " + ", ".join(blockers))
        else:
            path = tuple(route.path)
            width_inches = route.inches
            for profile in occupants:
                if route.inches < profile.travel_width_inches:
                    blocked.append(
                        f"aisle too narrow for {profile.title} "
                        f"({route.inches:.1f} in < {profile.travel_width_inches:.1f} in body width)"
                    )

        turning_blocked, turning = _turning_blockers(
            graph, measure, stop, list(path) if path else [], occupants
        )
        blocked.extend(turning_blocked)

        if mesh is not None and path:
            mesh_checked = True
            for profile in occupants:
                radius = to_meters(profile.travel_width_inches / 2)
                sampled = ensure_spacing(list(path), radius)
                if mesh.collides(sampled, profile.travel_width_inches / 2):
                    mesh_collision = True
                    blocked.append(
                        "raw capture collides with the swept body along the "
                        f"path ({profile.title})"
                    )
                    break

        obstructions = _line_obstructions(graph, target, stop, support)
        if obstructions:
            blocked.append(
                "below-reach obstruction between the approach and the target: "
                + ", ".join(obstructions)
            )

    if not floor_ok:
        unverified.append("the floor is unobserved; no support under the route")
    for record in reaches:
        if record.status == "unmeasured":
            unverified.append(
                "target height or "
                f"{record.occupant_title} personal reach is unmeasured"
            )
        elif record.status == "beyond_personal_reach":
            blocked.append(
                f"target above personal reach for {record.occupant_title} "
                f"({record.target_height_inches:.1f} in > "
                f"{record.personal_reach_inches:.1f} in assumed reach)"
            )
        if record.horizontal_status == "beyond_body_envelope":
            blocked.append(
                f"the approach stop stands {record.horizontal_distance_inches:.1f} in "
                f"from the target; beyond the {record.occupant_title} body "
                f"envelope of {record.body_envelope_radius_inches:.1f} in"
            )

    if blocked:
        status: ApproachStatus = "blocked"
    elif unverified:
        status = "needs_verification"
    else:
        status = "clear"

    return ApproachResult(
        target_id=target.id,
        status=status,
        reasons=tuple([*blocked, *unverified]),
        approach_stop=stop,
        path=path,
        aisle_width_inches=width_inches,
        turning_space_inches=turning,
        obstruction_labels=obstructions,
        floor_supported=floor_ok,
        mesh_checked=mesh_checked,
        mesh_collision=mesh_collision,
        reaches=reaches,
        unverified=tuple(unverified),
    )


__all__ = [
    "ApproachResult",
    "ReachRecord",
    "evaluate_approach",
    "suggestion_stop",
    "support_of",
    "target_height_inches",
]
