"""The owner-facing approach endpoint (K): one measured journey to one target.

Wraps R's conservative evaluator without inventing inputs. The occupant comes
from the screening catalog; a person-provided horizontal reach rides with its
provenance, and without it the answer says unmeasured. No legal claim.
"""

from __future__ import annotations

import dataclasses
import uuid

from standardphysics_agents.fix import (
    MANUAL_WHEELCHAIR,
    POWER_WHEELCHAIR,
    HorizontalReach,
    OccupantProfile,
    evaluate_approach,
)
from standardphysics_contracts import (
    ApproachReport,
    ApproachRequest,
    ReachReport,
    SceneGraph,
    Vec3,
)

from . import repository as repo
from .db import Database
from .errors import ApiProblem

OCCUPANT_CATALOG: dict[str, OccupantProfile] = {
    MANUAL_WHEELCHAIR.id: MANUAL_WHEELCHAIR,
    POWER_WHEELCHAIR.id: POWER_WHEELCHAIR,
    "manual-wheelchair": MANUAL_WHEELCHAIR,
    "power-wheelchair": POWER_WHEELCHAIR,
}


def evaluate(
    database: Database,
    stages,
    scan_id: uuid.UUID,
    base_revision: int,
    body: ApproachRequest,
) -> ApproachReport:
    with database.connect() as connection:
        if not repo.scan_exists(connection, scan_id):
            raise ApiProblem(404, "no scan")
        latest = repo.get_revision(connection, scan_id)
        if latest is None:
            raise ApiProblem(404, "no such revision")
        if latest["revision"] != base_revision:
            raise ApiProblem(409, "the shop changed since this revision; refresh and try again")
        graph: SceneGraph = repo.graph_of(repo.get_revision(connection, scan_id, base_revision))
        scenario = repo.get_scenario(connection, scan_id)

    if scenario is None:
        raise ApiProblem(409, "confirm a route before evaluating an approach")
    if not scenario.stops:
        raise ApiProblem(409, "confirm a route before evaluating an approach")
    try:
        target = graph.by_id(body.target_node_id)
    except KeyError:
        raise ApiProblem(404, "no such object") from None

    profile = OCCUPANT_CATALOG.get(body.occupant_profile)
    if profile is None:
        raise ApiProblem(400, "unknown occupant profile")
    if body.horizontal_reach_inches is not None and body.horizontal_reach_provenance is None:
        raise ApiProblem(400, "a horizontal reach needs its provenance")
    horizontal = (
        HorizontalReach(inches=body.horizontal_reach_inches, provenance=body.horizontal_reach_provenance)
        if body.horizontal_reach_inches is not None
        else None
    )
    occupant = dataclasses.replace(profile, horizontal_reach=horizontal)

    result = evaluate_approach(
        graph, target, scenario.stops[0], stages.measure,
        occupants=(occupant,),
        approach_stop=body.approach_stop,
    )
    return _report(result)


def _report(result) -> ApproachReport:
    return ApproachReport(
        target_id=result.target_id,
        status=result.status,
        reasons=list(result.reasons),
        approach_stop=result.approach_stop,
        path=[Vec3(x=p.x, y=p.y, z=p.z) for p in result.path] if result.path is not None else None,
        aisle_width_inches=result.aisle_width_inches,
        turning_space_inches=result.turning_space_inches,
        obstruction_labels=list(result.obstruction_labels),
        floor_supported=result.floor_supported,
        mesh_checked=result.mesh_checked,
        mesh_collision=result.mesh_collision,
        reaches=[
            ReachReport(
                occupant_title=reach.occupant_title,
                target_height_inches=reach.target_height_inches,
                vertical_status=reach.vertical_status,
                horizontal_distance_inches=reach.horizontal_distance_inches,
                horizontal_reach_inches=reach.horizontal_reach_inches,
                horizontal_reach_provenance=reach.horizontal_reach_provenance,
                horizontal_status=reach.horizontal_status,
            )
            for reach in result.reaches
        ],
        unverified=list(result.unverified),
    )
