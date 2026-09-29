"""The customer route for a scan: a suggestion to start from, and the one the owner confirms."""

from __future__ import annotations

import uuid

from standardphysics_agents.scenario_suggestion import suggest_path, suggest_scenario
from standardphysics_agents.staff_areas import with_staff_areas
from standardphysics_contracts import RouteLeg, RouteLegs, Scenario

from . import repository as repo
from . import repository_jobs as jobs_repo
from . import repository_revisions as revisions_repo
from .budgets import admit_new_job
from .db import Database
from .errors import ApiProblem
from .stages import Stages
from .worker import Worker
from .worker_handlers import ASSESS


def suggestion(database: Database, scan_id: uuid.UUID, destinations: list[str] | None = None) -> Scenario:
    with database.connect() as connection:
        if not repo.scan_exists(connection, scan_id):
            raise ApiProblem(404, "no scan")
        row = revisions_repo.get_revision(connection, scan_id)
    if row is None:
        raise ApiProblem(404, "not ready")
    graph = revisions_repo.graph_of(row)
    suggested = suggest_scenario(graph) if destinations is None else suggest_path(graph, destinations)
    return with_staff_areas(suggested, graph)


def saved(database: Database, scan_id: uuid.UUID) -> Scenario:
    """The owner's path, with the staff-only areas the checks will use filled in when they haven't said."""
    with database.connect() as connection:
        if not repo.scan_exists(connection, scan_id):
            raise ApiProblem(404, "no scan")
        found = revisions_repo.get_scenario(connection, scan_id)
        row = revisions_repo.get_revision(connection, scan_id)
    if found is None:
        raise ApiProblem(404, "not ready")
    return found if row is None else with_staff_areas(found, revisions_repo.graph_of(row))


def confirm(database: Database, worker: Worker, scan_id: uuid.UUID, scenario: Scenario) -> Scenario:
    with database.transaction() as connection:
        if not repo.scan_exists(connection, scan_id):
            raise ApiProblem(404, "no scan")
        row = revisions_repo.get_revision(connection, scan_id)
        if row is None:
            raise ApiProblem(409, "the shop is still being measured")
        admit_new_job(connection, worker.settings.max_queued_jobs)
        revisions_repo.save_scenario(connection, scan_id, scenario)
        repo.set_state(connection, scan_id, "checking")
        jobs_repo.queue_job_again(connection, scan_id, ASSESS, row["revision"])
    worker.wake()
    return scenario


def legs(database: Database, stages: Stages, scan_id: uuid.UUID, scenario: Scenario) -> RouteLegs:
    """The walking route for a path the owner is still shaping, the same route the checks measure."""
    with database.connect() as connection:
        if not repo.scan_exists(connection, scan_id):
            raise ApiProblem(404, "no scan")
        row = revisions_repo.get_revision(connection, scan_id)
    if row is None:
        raise ApiProblem(409, "the shop is still being measured")
    graph = revisions_repo.graph_of(row)
    walked = []
    for index, (start, end) in enumerate(zip(scenario.stops, scenario.stops[1:], strict=False)):
        width = stages.measure.route_clear_width(graph, scenario, index)
        walked.append(RouteLeg(from_stop=start.name, to_stop=end.name, path=width.path, reachable=width.reachable))
    return RouteLegs(legs=walked)
