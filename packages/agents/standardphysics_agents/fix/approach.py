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
)
from standardphysics_pipeline.occupancy import blocks_floor

from ..mesh_collision import MeshCollisionIndex
from .occupancy import (
    DEFAULT_OCCUPANTS,
    OccupantProfile,
    ensure_spacing,
)

ApproachStatus = Literal["clear", "blocked", "needs_verification"]

OBSTRUCTION_STEP_METERS = 0.05
"""How finely the occupant-to-target line is sampled for furniture in the way."""


@dataclass(frozen=True)
class ReachRecord:
    """Personal reach for one occupant; legal reach is decided in the assessment."""

    occupant_id: str
    occupant_title: str
    target_height_inches: float | None
    personal_reach_inches: float | None
    status: Literal["within_personal_reach", "beyond_personal_reach", "unmeasured"]

    @property
    def measured(self) -> bool:
        return self.status != "unmeasured"


@dataclass(frozen=True)
class ApproachResult:
    """Everything measured about one occupant journey to one target."""

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
    """The nodes the target hangs off or stands in.

    Everything whose footprint contains the target's position counts, plus the
    target itself and anything attached to it in the parent chain. These are
    the thin attachments that are not solid obstacles for a direct reach.
    """
    by_id = {node.id: node for node in graph.nodes}
    position = (target.transform.position.x, target.transform.position.y)
    supports = {
        other.id
        for other in graph.nodes
        if other.id != target.id
        and not lies_flat(other)
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


def _floor_present(graph: SceneGraph) -> bool:
    return any(lies_flat(node) for node in graph.nodes)


def _reach_record(target: SceneNode, profile: OccupantProfile) -> ReachRecord:
    height = target_height_inches(target)
    if height is None:
        return ReachRecord(
            occupant_id=profile.id,
            occupant_title=profile.title,
            target_height_inches=None,
            personal_reach_inches=profile.personal_reach_inches,
            status="unmeasured",
        )
    if profile.personal_reach_inches is None:
        return ReachRecord(
            occupant_id=profile.id,
            occupant_title=profile.title,
            target_height_inches=height,
            personal_reach_inches=None,
            status="unmeasured",
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
    )


def _arrival_turning(
    graph: SceneGraph,
    measure,
    path: list[Vec3],
    stop: Vec3,
    profile: OccupantProfile,
) -> tuple[float, Vec3]:
    """The widest turning circle anywhere on the measured arrival.

    The occupant turns somewhere on the way in, not necessarily with the body
    pressed against the wall at the stop, and a measured path that hugs a wall
    because everything else was wide is still an honest arrival. The screen
    walks the whole measured path backwards from the stop and takes the widest
    circle found; a route that never offers a full circle anywhere along it —
    the dead-end aisle — is blocked on turning.
    """
    best_inches = 0.0
    best_point: Vec3 = stop
    points = [stop, *path[::-1]]
    for back, forward in zip(points, points[1:]):
        turn = measure.turning_space(graph, back)
        available = min(turn.inches_wide, turn.inches_deep)
        if available > best_inches:
            best_inches, best_point = available, back
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
    reaches = tuple(_reach_record(target, profile) for profile in occupants)
    floor_ok = _floor_present(graph)

    stop = approach_stop
    if stop is None:
        stop = suggestion_stop(graph, target, occupants)

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
