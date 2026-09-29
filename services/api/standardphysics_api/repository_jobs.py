"""The job queue's rows: queueing, claiming, settling and recovering jobs, and each attempt's history.

A claim is one `UPDATE ... RETURNING` statement, so two loops can never take
the same job; callers run it inside `Database.transaction`, whose `BEGIN
IMMEDIATE` holds the write lock from the start.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from . import repository as repo


def other_running_job(connection: sqlite3.Connection, scan_id: uuid.UUID, job_id: int | None = None) -> bool:
    """True when a job for this scan is running, not counting the one given."""
    return connection.execute(
        "SELECT 1 FROM jobs WHERE scan_id = ? AND state = 'running' AND id IS NOT ?", (str(scan_id), job_id)
    ).fetchone() is not None


def queued_job_count(connection: sqlite3.Connection) -> int:
    return connection.execute("SELECT COUNT(*) FROM jobs WHERE state = 'queued'").fetchone()[0]


def has_pending_process_job(connection: sqlite3.Connection, scan_id: uuid.UUID) -> bool:
    """True while a semantic job is queued or running. Kind string matches worker_handlers.PROCESS."""
    return (
        connection.execute(
            "SELECT 1 FROM jobs WHERE scan_id = ? AND kind = 'process' AND state IN ('queued', 'running') LIMIT 1",
            (str(scan_id),),
        ).fetchone()
        is not None
    )


def latest_process_job(connection: sqlite3.Connection, scan_id: uuid.UUID) -> sqlite3.Row | None:
    """The newest process job row, or None when the scan has never queued one."""
    return connection.execute(
        "SELECT * FROM jobs WHERE scan_id = ? AND kind = 'process' ORDER BY id DESC LIMIT 1",
        (str(scan_id),),
    ).fetchone()


def set_job_binding(
    connection: sqlite3.Connection,
    job_id: int,
    input_hash: str | None,
    note: str | None,
) -> None:
    """Record which input manifest this run consumed, and its visible outcome.

    Runs at claim time and again at publish, so a fresh attempt starts from a
    clean latest view: the previous attempt's note and request receipts are
    cleared here, and only this attempt's own writes land afterwards.
    """
    connection.execute(
        "UPDATE jobs SET input_hash = ?, note = ?, model_requests_json = NULL WHERE id = ?",
        (input_hash, note, job_id),
    )


def set_job_requests(connection: sqlite3.Connection, job_id: int, requests_json: str | None) -> None:
    """Persist what every real detector request was: provider, model, provider
    request id and usage. Categories and counts only; never a secret or a pixel."""
    connection.execute(
        "UPDATE jobs SET model_requests_json = ? WHERE id = ?",
        (requests_json, job_id),
    )


def record_job_attempt(
    connection: sqlite3.Connection,
    job_id: int,
    attempt: int,
    scan_id: uuid.UUID,
) -> None:
    """Snap the finished job row into immutable per-attempt history.

    Each attempt gets its own (job, attempt) row that is never updated: what
    the attempt consumed, how it ended and which provider requests it made.
    The mutable jobs row stays the latest active view and is cleared at each
    claim, so an empty or failed attempt can never inherit the previous
    attempt's receipts.
    """
    row = connection.execute(
        "SELECT state, error, input_hash, note, model_requests_json FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        return
    connection.execute(
        "INSERT INTO job_attempts (job_id, attempt, scan_id, input_hash, state, error, note,"
        " model_requests_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (job_id, attempt) DO NOTHING",
        (
            job_id,
            attempt,
            str(scan_id),
            row["input_hash"],
            row["state"],
            row["error"],
            row["note"],
            row["model_requests_json"],
            repo.now(),
        ),
    )


def process_job_states(connection: sqlite3.Connection, scan_id: uuid.UUID) -> tuple[str, ...]:
    """Every state a process job has been in for this scan, newest first."""
    rows = connection.execute(
        "SELECT state FROM jobs WHERE scan_id = ? AND kind = 'process' ORDER BY id DESC",
        (str(scan_id),),
    ).fetchall()
    return tuple(row["state"] for row in rows)


def enqueue_job(connection: sqlite3.Connection, scan_id: uuid.UUID, kind: str, revision: int) -> None:
    queued_at = repo.now()
    connection.execute(
        "INSERT OR IGNORE INTO jobs (scan_id, kind, revision, state, created_at, queued_at)"
        " VALUES (?, ?, ?, 'queued', ?, ?)",
        (str(scan_id), kind, revision, queued_at, queued_at),
    )


def queue_job_again(connection: sqlite3.Connection, scan_id: uuid.UUID, kind: str, revision: int) -> None:
    """Queue a job whether or not it ran before, unless it is running now."""
    queued_at = repo.now()
    connection.execute(
        "INSERT INTO jobs (scan_id, kind, revision, state, created_at, queued_at) VALUES (?, ?, ?, 'queued', ?, ?)"
        " ON CONFLICT (scan_id, kind, revision) DO UPDATE SET state = 'queued', error = NULL,"
        " interruptions = 0, queued_at = excluded.queued_at WHERE jobs.state != 'running'",
        (str(scan_id), kind, revision, queued_at, queued_at),
    )


def oldest_queued_job_seconds(connection: sqlite3.Connection) -> float | None:
    """How long the job at the front of the queue has waited, or None when nothing is queued.

    A number that keeps growing while the worker reports itself idle means jobs
    are arriving and nothing is taking them.
    """
    row = connection.execute(
        "SELECT MIN(COALESCE(queued_at, created_at)) AS since FROM jobs WHERE state = 'queued'"
    ).fetchone()
    if row["since"] is None:
        return None
    return round((datetime.now(UTC) - datetime.fromisoformat(row["since"])).total_seconds(), 1)


BLENDER_KINDS = ("texture", "display")
"""The kinds that run Blender, which share one lane so a 4 GB machine never holds two Blenders at once."""
LANED_KINDS = (*BLENDER_KINDS, "rearrange", "furniture", "simulate")
"""Job kinds that run on a worker thread of their own, never the main one."""


def _lane_filter(texture_only: bool | None, kind: str | None) -> tuple[str, tuple]:
    if kind is not None:
        return "kind = ?", (kind,)
    if texture_only:
        return "kind IN (" + ", ".join("?" for _ in BLENDER_KINDS) + ")", BLENDER_KINDS
    if texture_only is False:
        return "kind NOT IN (" + ", ".join("?" for _ in LANED_KINDS) + ")", LANED_KINDS
    return "1=1", ()


def claim_job(
    connection: sqlite3.Connection, texture_only: bool | None = None, *, kind: str | None = None
) -> sqlite3.Row | None:
    """The job queued longest ago on a lane: textures, one kind, everything but the laned kinds, or anything.

    Ordered by when it was queued, not by its id: queueing a job again reuses
    its row, and a render re-queued on a row from last week must not go ahead
    of a shop uploaded a minute ago. A measuring job goes before every other
    kind on its lane, so someone who just walked their shop is not waiting on
    the re-check of every older shop that a deploy queues.
    """
    lane, parameters = _lane_filter(texture_only, kind)
    return connection.execute(
        "UPDATE jobs SET state = 'running', attempts = attempts + 1"
        " WHERE id = (SELECT id FROM jobs WHERE state = 'queued'"
        f" AND {lane} ORDER BY kind != 'process', COALESCE(queued_at, created_at), id LIMIT 1)"
        " RETURNING id, scan_id, kind, revision, attempts",
        parameters,
    ).fetchone()


def finish_job(connection: sqlite3.Connection, job_id: int, error: str | None = None) -> None:
    state = "failed" if error else "done"
    connection.execute("UPDATE jobs SET state = ?, error = ? WHERE id = ?", (state, error, job_id))


def fail_running_job(connection: sqlite3.Connection, job_id: int, error: str) -> bool:
    """Fail a job only if it is still running, so an outcome already written is never overwritten."""
    cursor = connection.execute(
        "UPDATE jobs SET state = 'failed', error = ? WHERE id = ? AND state = 'running'", (error, job_id)
    )
    return cursor.rowcount > 0


def requeue_running_job(connection: sqlite3.Connection, job_id: int) -> bool:
    """Put a claimed job back at its place in the queue, if nothing has settled it since."""
    cursor = connection.execute(
        "UPDATE jobs SET state = 'queued', queued_at = ? WHERE id = ? AND state = 'running'", (repo.now(), job_id)
    )
    return cursor.rowcount > 0


def retry_failed_jobs(connection: sqlite3.Connection, scan_id: uuid.UUID) -> None:
    """Queue again whichever stage failed, and show the state that stage runs in."""
    kinds = {
        row["kind"]
        for row in connection.execute(
            "SELECT kind FROM jobs WHERE scan_id = ? AND state = 'failed'"
            " AND kind NOT IN ('display', 'simulate', 'texture', 'rearrange')",
            (str(scan_id),),
        )
    }
    if not kinds:
        return
    state = "measuring" if "process" in kinds else "checking"
    connection.execute("UPDATE scans SET state = ? WHERE id = ?", (state, str(scan_id)))
    connection.execute(
        "UPDATE jobs SET state = 'queued', error = NULL, interruptions = 0, queued_at = ?"
        " WHERE scan_id = ? AND state = 'failed'"
        " AND kind NOT IN ('display', 'simulate', 'texture', 'rearrange')",
        (repo.now(), str(scan_id)),
    )


INTERRUPTED_SIMULATION = "Simulation interrupted; start a new run to continue"


def fail_interrupted_simulations(connection: sqlite3.Connection) -> None:
    """Fail every simulation a stopped process left running, rather than queueing it again.

    A simulation spends paid TypeSafe and Astra calls against a per-run budget
    that lives only in the process running it. Queued again, it would start
    from nothing with a fresh budget and could spend the owner's limit a second
    time without anyone asking, so the owner starts the new run.
    """
    connection.execute(
        "UPDATE jobs SET state = 'failed', error = ? WHERE kind = 'simulate' AND state = 'running'",
        (INTERRUPTED_SIMULATION,),
    )


MAX_INTERRUPTIONS = 3
"""How many runs of one job a restart may cut short before the job is failed rather than queued again."""
DERIVED_KINDS = ("display", "simulate", "texture")
"""Job kinds whose failure leaves the scan's own state alone. Each is asked for again on its own."""


def requeue_interrupted_jobs(
    connection: sqlite3.Connection, max_interruptions: int = MAX_INTERRUPTIONS
) -> list[sqlite3.Row]:
    """Queue again every job a stopped process left running, and return the ones stopped instead.

    Each job left running counts one more interrupted run on its row. A job
    whose input kills the whole container would otherwise run, take the server
    down, and be queued again by the next start for ever, so at
    `max_interruptions` it is failed with the count in its error. The retry
    route queues it again with the count cleared, once the cause is fixed.
    """
    connection.execute("UPDATE jobs SET interruptions = interruptions + 1 WHERE state = 'running'")
    stopped = _fail_jobs_interrupted_too_often(connection, max_interruptions)
    connection.execute("UPDATE jobs SET state = 'queued', queued_at = ? WHERE state = 'running'", (repo.now(),))
    return stopped


def _fail_jobs_interrupted_too_often(connection: sqlite3.Connection, max_interruptions: int) -> list[sqlite3.Row]:
    stopped = connection.execute(
        "UPDATE jobs SET state = 'failed', error = 'Stopped after ' || interruptions || ' interrupted runs:"
        " the server stopped during each one. Retry once the cause is fixed.'"
        " WHERE state = 'running' AND interruptions >= ? RETURNING id, scan_id, kind, attempts, interruptions",
        (max_interruptions,),
    ).fetchall()
    for job in stopped:
        scan_id = uuid.UUID(job["scan_id"])
        record_job_attempt(connection, job["id"], job["attempts"], scan_id)
        if job["kind"] not in DERIVED_KINDS:
            repo.set_state(connection, scan_id, "failed")
    return stopped


def display_pending(connection: sqlite3.Connection, scan_id: uuid.UUID) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM jobs WHERE scan_id = ? AND kind IN ('process', 'assess', 'display')"
            " AND state IN ('queued', 'running') LIMIT 1",
            (str(scan_id),),
        ).fetchone()
        is not None
    )
