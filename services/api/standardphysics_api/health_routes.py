"""Health checks for a load balancer, a readiness probe and whoever is on call."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from standardphysics_agents import tracing_status

from . import drain
from . import repository_jobs as jobs_repo
from .db import Database
from .furniture import furniture_health
from .worker import Worker


def install_health_routes(app: FastAPI, database: Database, worker: Worker, commit: str) -> None:
    @app.get("/health")
    def health():
        """Liveness, reachable without a session, so a load balancer and the container healthcheck can ask.

        It touches the database, because a process that is listening but cannot
        read its own scans is not healthy in any way that matters. It fails when
        a worker loop has died, since then uploads are accepted and never
        measured. It stays healthy while a loop is busy with a fifteen-minute
        bake, and in a second process that found the worker lock taken, because
        restarting either one would fix nothing. A loop that is stalled or
        running a job past its deadline is named in the body but not failed
        here. Both are judged from timing alone, and a restart in the middle of
        a slow job throws its work away. /health/ready fails on them.
        """
        with database.connect() as connection:
            connection.execute("SELECT 1 FROM scans LIMIT 1").fetchone()
        state = worker.summary()
        body = {"status": "ok" if state != "stopped" else "worker stopped", "worker": state}
        return JSONResponse(body, status_code=503 if state == "stopped" else 200)

    @app.get("/health/ready")
    def health_ready():
        """Whether queued work is getting done: 503 while any worker loop has died, is stuck
        outside a job, or is running a job past its kind's deadline."""
        with database.connect() as connection:
            connection.execute("SELECT 1 FROM scans LIMIT 1").fetchone()
        problems = worker.problems()
        body = {"status": "degraded" if problems else "ready", "problems": problems}
        return JSONResponse(body, status_code=503 if problems else 200)

    @app.get("/health/details")
    def health_details() -> dict:
        """What each worker loop is doing, how long since it last beat, how long the queue has waited,
        which commit this server was built from, whether tracing came up, the send failures Weave
        logged, whether a deploy is draining it, and whether furniture refinement can run here."""
        with database.connect() as connection:
            oldest = jobs_repo.oldest_queued_job_seconds(connection)
        problems = worker.problems()
        return {
            "status": "degraded" if problems else "ok",
            "problems": problems,
            "worker": worker.status(),
            "oldest_queued_job_seconds": oldest,
            "commit": commit,
            "tracing": tracing_status(),
            "draining": drain.is_draining(worker.settings.data_dir),
            "furniture_refinement": furniture_health(worker.settings),
        }
