"""Two background threads that run queued jobs in order: one for photo bakes, one for the rest.

Run one API process per database. Before it touches the queue the worker takes
an exclusive lock on a file beside the database; a second process finds the
lock held, says so in the log, and serves requests without running any job.
Jobs are claimed atomically, and at startup every job left running is queued
again, except a simulation, which is failed so that a restart never spends a
second budget of paid model calls (`repo.fail_interrupted_simulations`). The
lock is what makes that safe: no other live process can be running one of them.

Neither loop stops on an error. A job's own failure is recorded on its row; an
error outside any job, such as a database that stays locked, is logged and the
loop tries again after a wait that doubles up to a minute. Writing how a job
ended waits out a locked database for SETTLE_PATIENCE_SECONDS at most and any
other database error not at all; a job that can't be settled is left running
for the next start to queue again, and /health/ready names it until then.

On the server every job's heavy stage runs in a spawned child process
(`in_own_process`): photo bakes when `bake_in_own_process` is on, the other
kinds when `jobs_in_own_process` is on. The child gets the settings, never a
live connection, and opens the database and the store itself; a child still
running at its job's deadline is killed with everything it started, the job is
failed, and the loop moves on to the next job.
"""

from __future__ import annotations

import json
import logging
import math
import multiprocessing
import os
import pathlib
import signal
import sqlite3
import threading
import time
import traceback
import urllib.error
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from standardphysics_contracts import SimulationRequest

from . import evidence, guest_sweep
from . import repository as repo
from .db import Database
from .errors import ApiProblem
from .notifications import LoggedNotifier, Notifier, Push, notifier_from
from .settings import Settings
from .simulations import SIMULATE, queue_simulation, run_simulation
from .stages import DiscoveryOutcome, Stages, configured_stages
from .store import ArtifactStore, ScanQuota
from .textures import TEXTURE, maybe_queue_texture, run_texture
from .worker_lock import WorkerLock

log = logging.getLogger(__name__)

PROCESS, ASSESS, DISPLAY = "process", "assess", "display"


SWEEP_SECONDS = 3600.0
IDLE_WAIT_SECONDS = 2.0
FIRST_RETRY_SECONDS = 1.0
LONGEST_RETRY_SECONDS = 60.0
STALLED_AFTER_SECONDS = 120.0
"""An idle loop beats at least every IDLE_WAIT_SECONDS and waits at most
LONGEST_RETRY_SECONDS between retries, so an idle loop that has not beaten for
this long is stuck somewhere. A loop running a job is never called stalled,
because a photo bake takes up to fifteen minutes and the loop beats only when
it ends. A job that runs past its kind's deadline is reported overdue instead."""
PROBLEM_STATES = ("stopped", "stalled", "overdue")
"""Loop states that mean jobs are not getting done, worst first."""
LOOP_NAMES = {False: "jobs", True: "textures"}
MAX_CLAIMS_BEFORE_START = 3
"""How many times a job may be claimed and put back because of an error before it ran."""
SETTLE_PATIENCE_SECONDS = 60.0
"""How long writing a job's outcome waits out a database another writer holds before giving up."""
TRANSIENT_ATTEMPTS = 3
"""How many times a job runs when it keeps meeting an error that goes away by itself: a
database another writer holds past its busy timeout, or a provider request that timed out."""
LONGEST_CHILD_ERROR = 2000
"""How many characters of a child's error cross back to the worker. A pipe holds far more, so
the child's last write never blocks waiting for a parent that is itself waiting for the child to exit."""


class _Backoff:
    """Waits that double from FIRST_RETRY_SECONDS up to LONGEST_RETRY_SECONDS."""

    def __init__(self):
        self._next = FIRST_RETRY_SECONDS

    def delay(self) -> float:
        current = min(self._next, LONGEST_RETRY_SECONDS)
        self._next = current * 2
        return current

    def reset(self) -> None:
        self._next = FIRST_RETRY_SECONDS


class JobOverran(RuntimeError):
    """A job ran past its kind's deadline and was stopped at the next stage boundary."""


@dataclass(frozen=True)
class RunningJob:
    kind: str
    id: int
    started_at: float
    deadline_seconds: float

    def running_seconds(self, now: float) -> float:
        return now - self.started_at

    def overdue(self, now: float) -> bool:
        return self.running_seconds(now) > self.deadline_seconds

    def seconds_left(self, now: float) -> float | None:
        """How long the job may still run, or None when its kind has no deadline."""
        if math.isinf(self.deadline_seconds):
            return None
        return max(self.deadline_seconds - self.running_seconds(now), 0.0)

    def overran(self) -> JobOverran:
        limit = _duration(self.deadline_seconds)
        return JobOverran(f"The {self.kind} job did not finish within {limit} and was stopped")

    def stop_if_overdue(self) -> None:
        if self.overdue(time.monotonic()):
            raise self.overran()


@dataclass
class LoopPulse:
    """What one worker loop is doing, kept in memory so /health can read it without the database."""

    thread: threading.Thread | None = None
    beat_at: float | None = None
    job: RunningJob | None = None
    """The job running now. One attribute, so a reader never sees half of it."""

    def beat(self) -> None:
        self.beat_at = time.monotonic()

    def begin(self, job: RunningJob) -> None:
        self.job = job

    def end(self) -> None:
        self.job = None
        self.beat()

    def state(self, now: float) -> str:
        if self.thread is None:
            return "not_started"
        if not self.thread.is_alive():
            return "stopped"
        if self.job is not None:
            return "overdue" if self.job.overdue(now) else "busy"
        if self.beat_at is None or now - self.beat_at > STALLED_AFTER_SECONDS:
            return "stalled"
        return "idle"

    def report(self, now: float) -> dict:
        job = self.job
        return {
            "state": self.state(now),
            "heartbeat_seconds": None if self.beat_at is None else round(now - self.beat_at, 1),
            "job": None if job is None else {
                "kind": job.kind,
                "id": job.id,
                "running_seconds": round(job.running_seconds(now), 1),
            },
        }


@dataclass
class _JobOutcome:
    error: str | None = None
    follow_up: bool = False
    """Queue the next derived job after this one settles; its row is still running now."""


class _UnusableEvidence(Exception):
    """The evidence artifacts exist but do not agree with each other, so no
    semantic pass may consume them. Visible on the job and the evidence status;
    a new upload that repairs the pairing queues the next attempt."""


class Worker:
    def __init__(
        self,
        database: Database,
        store: ArtifactStore,
        stages: Stages,
        settings: Settings,
    ):
        self.database, self.store, self.stages = database, store, stages
        self.settings = settings
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.pulses = {texture_only: LoopPulse() for texture_only in LOOP_NAMES}
        self.lock = WorkerLock(database.path)
        self._standby = False
        self.notifier: Notifier = LoggedNotifier()
        self._swept_at: float | None = None
        self._on_this_thread = threading.local()
        self._unsettled: dict[int, str] = {}
        """Jobs whose outcome could not be written, by id, with the error. Cleared by a restart,
        which queues them again."""
        self.stages_in_child: Callable[[Settings], Stages] = configured_stages
        """Builds the stages a job run in its own process uses. The child finds it by module and
        name, so it must be a module-level function."""

    def start(self) -> None:
        if not self.lock.acquire():
            self._standby = True
            log.error(
                "another worker already holds %s (process %s), so this process serves requests but runs no jobs;"
                " run one API process per database",
                self.lock.path,
                self.lock.holder(),
            )
            return
        try:
            self._recover_interrupted_jobs()
        except Exception:
            self.lock.release()
            raise
        for texture_only, pulse in self.pulses.items():
            pulse.thread = threading.Thread(
                target=self._loop,
                args=(texture_only,),
                name=f"standardphysics-{LOOP_NAMES[texture_only]}",
                daemon=True,
            )
            pulse.beat()
            pulse.thread.start()

    def _recover_interrupted_jobs(self) -> None:
        with self.database.transaction() as connection:
            repo.fail_interrupted_simulations(connection)
            repo.requeue_interrupted_jobs(connection)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        threads = [pulse.thread for pulse in self.pulses.values() if pulse.thread is not None]
        for thread in threads:
            thread.join(timeout=5)
        if not any(thread.is_alive() for thread in threads):
            self.lock.release()

    def status(self) -> dict:
        """Whether this process holds the queue, and what each loop is doing."""
        now = time.monotonic()
        return {
            "lock": "held" if self.lock.held else "standby" if self._standby else "free",
            "loops": {LOOP_NAMES[texture_only]: pulse.report(now) for texture_only, pulse in self.pulses.items()},
        }

    def problems(self) -> list[str]:
        """Each loop that has died, is stuck outside any job, or is running a job past its deadline,
        and each job whose outcome could not be written."""
        if self._stop.is_set():
            return []
        now = time.monotonic()
        states = {LOOP_NAMES[texture_only]: pulse.state(now) for texture_only, pulse in self.pulses.items()}
        loops = [f"the {name} loop is {state}" for name, state in states.items() if state in PROBLEM_STATES]
        return loops + [_unsettled_problem(job_id, error) for job_id, error in dict(self._unsettled).items()]

    def summary(self) -> str:
        """One word for /health: the worst loop problem first, otherwise whether this process holds the queue."""
        now = time.monotonic()
        states = set() if self._stop.is_set() else {pulse.state(now) for pulse in self.pulses.values()}
        worst = next((state for state in PROBLEM_STATES if state in states), None)
        if worst is not None:
            return worst
        if self.lock.held:
            return "running"
        return "standby" if self._standby else "not_started"

    def wake(self) -> None:
        self._wake.set()

    def label_inputs(self, scan_id: uuid.UUID) -> tuple[list[pathlib.Path], pathlib.Path | None, pathlib.Path | None]:
        """Return uploaded frame and pose artifacts for the built-in labeler."""
        with self.database.connect() as connection:
            frames = repo.artifacts_of_kind(connection, scan_id, "frames")
            poses = repo.artifact_of_kind(connection, scan_id, "poses")
            lidar = repo.artifact_of_kind(connection, scan_id, "lidar_mesh")
        frame_paths = [self.store.artifact_path(scan_id, artifact.id) for artifact in frames]
        poses_path = self.store.artifact_path(scan_id, poses.id) if poses is not None else None
        return frame_paths, poses_path, self.store.artifact_path(scan_id, lidar.id) if lidar else None

    def drain(self) -> None:
        """Run every queued job on the calling thread. Tests use this."""
        while self.run_once():
            pass

    def run_once(self, texture_only: bool | None = None) -> bool:
        with self.database.transaction() as connection:
            job = repo.claim_job(connection, texture_only)
        if job is None:
            return False
        pulse = self.pulses[texture_only] if texture_only is not None else LoopPulse()
        running = RunningJob(job["kind"], job["id"], time.monotonic(), self.settings.job_deadline_seconds(job["kind"]))
        pulse.begin(running)
        self._on_this_thread.job = running
        try:
            self._settle(job)
        finally:
            self._on_this_thread.job = None
            pulse.end()
        return True

    def _checkpoint(self) -> None:
        """A stage boundary: stop the job this thread is running if it is past its deadline.

        Python can't interrupt a thread from outside, so an in-thread job is
        stopped here, between stages, and what the late stage produced is not saved.
        A job run in its own process has no job on its thread, so this does nothing
        there: the worker kills the whole process at the deadline instead.
        """
        running: RunningJob | None = getattr(self._on_this_thread, "job", None)
        if running is not None:
            running.stop_if_overdue()

    def _settle(self, job) -> None:
        """Run a claimed job and write how it ended, whatever raises on the way.

        A row left running can't be queued again and keeps its scan from being
        deleted, so an error before the job starts puts it back in the queue,
        and an error once it has started fails it with that error. Either way
        the error is raised again for the loop to log and back off on.
        """
        scan_id = uuid.UUID(job["scan_id"])
        if self._closed_before_start(job, scan_id):
            return
        try:
            outcome = self._run(job)
            self._record_outcome(job, scan_id, outcome)
        except BaseException as error:
            self._fail_unrecorded(job, scan_id, error)
            raise
        if self._delete_if_asked(scan_id):
            return
        if outcome.follow_up and outcome.error is None:
            self._queue_follow_up_if_due(scan_id)

    def _closed_before_start(self, job, scan_id: uuid.UUID) -> bool:
        try:
            return self._delete_if_asked(scan_id, job["id"])
        except BaseException as error:
            self._release_unstarted(job, scan_id, error)
            raise

    def _release_unstarted(self, job, scan_id: uuid.UUID, error: BaseException) -> None:
        """Queue a job again that an error stopped before it ran, up to MAX_CLAIMS_BEFORE_START claims.

        The cap is for an error that is not going away, which would otherwise
        claim and release the same job for ever.
        """
        if job["attempts"] < MAX_CLAIMS_BEFORE_START:
            self._settle_or_leave_for_restart(job, lambda connection: repo.requeue_running_job(connection, job["id"]))
            return
        self._fail_unrecorded(job, scan_id, error)

    def _fail_unrecorded(self, job, scan_id: uuid.UUID, error: BaseException) -> None:
        message = f"{type(error).__name__}: {error}"

        def fail(connection) -> None:
            if repo.fail_running_job(connection, job["id"], message):
                repo.record_job_attempt(connection, job["id"], job["attempts"], scan_id)

        self._settle_or_leave_for_restart(job, fail)

    def _settle_or_leave_for_restart(self, job, write: Callable[[sqlite3.Connection], object]) -> None:
        """The last attempt to settle a job. If even this can't be written, the
        worker lock guarantees the next start finds the row running and queues it."""
        try:
            self._write_through_locks(write, f"job {job['id']}")
        except Exception as error:
            self._unsettled[job["id"]] = f"{type(error).__name__}: {error}"
            log.error(
                "job %s is left running until the next start queues it again:\n%s",
                job["id"],
                traceback.format_exc(),
            )

    def _record_outcome(self, job, scan_id: uuid.UUID, outcome: _JobOutcome) -> None:
        def record(connection) -> None:
            repo.finish_job(connection, job["id"], outcome.error)
            repo.record_job_attempt(connection, job["id"], job["attempts"], scan_id)

        self._write_through_locks(record, f"job {job['id']}")

    def _write_through_locks(self, write: Callable[[sqlite3.Connection], object], what: str) -> None:
        """Run one write in a transaction, waiting out a locked database for SETTLE_PATIENCE_SECONDS.

        Any other database error, such as a missing table, a disk I/O error or a
        read-only file, won't go away by waiting, so it is raised at once. A lock
        still held when the patience runs out, or when the worker is stopping, is
        raised too.
        """
        backoff = _Backoff()
        give_up_at = time.monotonic() + SETTLE_PATIENCE_SECONDS
        while True:
            try:
                with self.database.transaction() as connection:
                    write(connection)
                return
            except sqlite3.OperationalError as error:
                delay = backoff.delay()
                if not _is_lock_contention(error) or self._stop.is_set() or time.monotonic() + delay > give_up_at:
                    raise
                log.warning("could not record %s (%s); trying again in %.1f s", what, error, delay)
                self._stop.wait(delay)

    def _delete_if_asked(self, scan_id: uuid.UUID, claimed_job: int | None = None) -> bool:
        """Finish deleting a scan its owner deleted while a job of its was running.

        A job claimed for a deleted scan is closed without running. The scan
        goes once no job of its is running, which is whenever the last one ends.
        """
        with self.database.transaction() as connection:
            if not repo.marked_for_deletion(connection, scan_id):
                return False
            if claimed_job is not None:
                repo.finish_job(connection, claimed_job, "The shop was deleted")
            if repo.other_running_job(connection, scan_id):
                return True
            repo.delete_scan(connection, scan_id)
        self.store.remove_scan(scan_id)
        return True

    def _queue_follow_up_if_due(self, scan_id: uuid.UUID) -> None:
        """After a process job, queue the next semantic run exactly once if the
        evidence manifest advanced while this job was running."""
        with self.database.transaction() as connection:
            latest = repo.latest_bundle(connection, scan_id)
            if latest is None or not latest.complete:
                return
            if latest.semantic_processed_hash == latest.manifest_hash:
                return
            if repo.has_pending_process_job(connection, scan_id):
                return
            repo.queue_job_again(connection, scan_id, PROCESS, 0)
            self.wake()

    def _loop(self, texture_only: bool = False) -> None:
        pulse = self.pulses[texture_only]
        backoff = _Backoff()
        while not self._stop.is_set():
            pulse.beat()
            try:
                self._tick(texture_only)
            except Exception:
                delay = backoff.delay()
                log.error(
                    "worker loop %s failed; trying again in %.1f s:\n%s",
                    LOOP_NAMES[texture_only],
                    delay,
                    traceback.format_exc(),
                )
                self._stop.wait(delay)
                continue
            backoff.reset()

    def _tick(self, texture_only: bool) -> None:
        if self.run_once(texture_only):
            return
        if not texture_only:
            self._sweep_due_settled()
            self._sweep_hourly()
        self._wake.wait(timeout=IDLE_WAIT_SECONDS)
        self._wake.clear()

    def _sweep_hourly(self) -> None:
        """Delete expired guest shops and staged uploads nothing is writing any more."""
        if self._swept_at is not None and time.monotonic() - self._swept_at < SWEEP_SECONDS:
            return
        self._swept_at = time.monotonic()
        try:
            guest_sweep.sweep(self.database, self.store, self.notifier, datetime.now(UTC))
        except Exception:
            log.warning("guest sweep failed:\n%s", traceback.format_exc())
        try:
            self.store.remove_abandoned_staging(self.settings.staging_max_age_seconds)
        except OSError:
            log.warning("staging sweep failed:\n%s", traceback.format_exc())

    def _tell_results_ready(self, scan_id: uuid.UUID) -> None:
        """One push, the first time a shop's results are ready. A re-check afterwards stays quiet."""
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT owner_id, results_told_at FROM scans WHERE id = ?", (str(scan_id),)
            ).fetchone()
            if row is None or row["owner_id"] is None or row["results_told_at"]:
                return
            told = datetime.now(UTC).isoformat()
            connection.execute("UPDATE scans SET results_told_at = ? WHERE id = ?", (told, str(scan_id)))
        push = Push(title="Your shop is measured", body="See what we found and what to fix.", scan_id=scan_id)
        try:
            self.notifier.send(self.database, uuid.UUID(row["owner_id"]), push)
        except Exception:
            log.warning("results push for %s failed:\n%s", scan_id, traceback.format_exc())

    def _sweep_due_settled(self) -> None:
        """Queue the one due recognition job for every quiet complete bundle.

        Without this, evidence that stops arriving never settles: the next
        trigger would have to be another request, which a finished upload never
        makes. The sweep reads the database each tick, so a restart re-derives
        the same decision with nothing persisted in memory.
        """
        try:
            with self.database.connect() as connection:
                due = evidence.due_semantic_scans(connection, self.settings.evidence_settle_seconds)
        except Exception:
            log.warning("due-settled sweep could not read scans:\n%s", traceback.format_exc())
            return
        queued = False
        for scan in due:
            try:
                with self.database.transaction() as connection:
                    queued = (
                        evidence.maybe_queue_semantic(
                            connection,
                            scan,
                            PROCESS,
                            settle_seconds=self.settings.evidence_settle_seconds,
                        )
                        == "queued"
                    ) or queued
            except Exception:
                log.warning("due-settled sweep skipped %s:\n%s", scan.id, traceback.format_exc())
        if queued:
            self.wake()

    def _run(self, job) -> _JobOutcome:
        scan_id = uuid.UUID(job["scan_id"])
        try:
            return _JobOutcome(follow_up=self._run_through_transient_errors(job, scan_id))
        except Exception as exc:
            log.error("job %s %s failed:\n%s", job["kind"], scan_id, traceback.format_exc())
            if job["kind"] not in (DISPLAY, SIMULATE, TEXTURE):
                with self.database.transaction() as connection:
                    repo.set_state(connection, scan_id, "failed")
            return _JobOutcome(error=_job_error(job["kind"], exc))

    def _run_through_transient_errors(self, job, scan_id: uuid.UUID) -> bool:
        """Run the job's stage, and run it again after a short wait when it met an error that
        goes away by itself, up to TRANSIENT_ATTEMPTS runs in all and never past its deadline."""
        backoff = _Backoff()
        attempt = 1
        while True:
            try:
                return self._call_handler(job)
            except Exception as error:
                if attempt >= TRANSIENT_ATTEMPTS or not is_transient(error) or self._stop.is_set():
                    raise
                delay = backoff.delay()
                log.warning("job %s %s met %r; running it again in %.1f s", job["kind"], scan_id, error, delay)
            self._stop.wait(delay)
            self._checkpoint()
            attempt += 1

    def _call_handler(self, job) -> bool:
        if job["kind"] != TEXTURE and self.settings.jobs_in_own_process:
            return self._in_child(job)
        return self.run_stage(job)

    def run_stage(self, job) -> bool:
        """Run the job's stage here and say whether a follow-up process job may be due."""
        scan_id, revision = uuid.UUID(job["scan_id"]), job["revision"]
        handler: Callable[..., bool] = {
            PROCESS: self._process,
            ASSESS: self._assess,
            DISPLAY: self._display,
            SIMULATE: self._simulate,
            TEXTURE: self._texture,
        }[job["kind"]]
        if job["kind"] == TEXTURE:
            return handler(scan_id=scan_id, build_id=revision, job=job)
        return handler(scan_id=scan_id, revision=revision, job=job)

    def _in_child(self, job) -> bool:
        """Run the job's stage in a process of its own, and kill it when the job reaches its deadline.

        The child writes the job's results to the database itself and may queue
        jobs of its own, so the loops are woken once it is done.
        """
        running = self._running_job(job)
        try:
            follow_up = in_own_process(
                run_job,
                self.settings,
                self.stages_in_child,
                dict(job),
                timeout_seconds=running.seconds_left(time.monotonic()),
            )
        except ChildTimedOut:
            raise running.overran() from None
        self.wake()
        return bool(follow_up)

    def _running_job(self, job) -> RunningJob:
        running: RunningJob | None = getattr(self._on_this_thread, "job", None)
        if running is not None:
            return running
        return RunningJob(job["kind"], job["id"], time.monotonic(), self.settings.job_deadline_seconds(job["kind"]))

    def _texture(self, scan_id, build_id, job=None) -> bool:
        if self.settings.bake_in_own_process:
            in_own_process(
                bake_photos, self.settings, scan_id, build_id, timeout_seconds=self.settings.bake_timeout_seconds
            )
        else:
            run_texture(self.database, self.store, self.stages, scan_id, build_id)
        return False

    def _simulate(self, scan_id: uuid.UUID, revision: int, job=None) -> bool:
        run_simulation(self.database, self.store, self.stages, scan_id, revision, self._checkpoint)
        return False

    def _process(self, scan_id: uuid.UUID, revision: int, job=None) -> bool:
        with self.database.connect() as connection:
            bundle = repo.latest_bundle(connection, scan_id)
            consumed = (bundle.version, bundle.manifest_hash) if bundle else None
        if consumed is not None:
            with self.database.transaction() as connection:
                repo.set_job_binding(connection, job["id"], consumed[1], None)
        association_state, association_failure, declared = evidence.association_state(
            self.database, self.store, scan_id
        )
        if declared and association_state == "failed":
            raise _UnusableEvidence(association_failure or "uploaded evidence does not parse")
        with self.database.connect() as connection:
            room_json = repo.artifact_of_kind(connection, scan_id, "room_json")
        if room_json is None:
            raise _UnusableEvidence("the scan has no room_json to measure")
        frame_paths, poses_path, lidar_mesh_path = self.label_inputs(scan_id)
        # With a declared manifest every state except not_started means the
        # pairing is unfilled or broken; such a run never counts as semantic.
        run_discovery = not declared or association_state == "not_started"
        graph, outcome = self.stages.ingest_with_report(
            self.store.artifact_path(scan_id, room_json.id),
            scan_id,
            frame_paths=frame_paths,
            poses_path=poses_path,
            lidar_mesh_path=lidar_mesh_path,
            run_discovery=run_discovery,
        )
        if not run_discovery:
            outcome = DiscoveryOutcome(deferred_reason=association_failure or association_state)
        self._checkpoint()
        with self.database.transaction() as connection:
            repo.save_revision(connection, graph, source="ingest")
            if run_discovery:
                self._mark_consumed_if_due(connection, scan_id, consumed)
            repo.set_job_binding(connection, job["id"], consumed[1] if consumed else None, outcome.note())
            if outcome.model_requests:
                repo.set_job_requests(
                    connection,
                    job["id"],
                    json.dumps(
                        [
                            request.model_dump(mode="json") if hasattr(request, "model_dump") else asdict(request)
                            for request in outcome.model_requests
                        ],
                        default=str,
                    ),
                )
        self._assess(scan_id=scan_id, revision=graph.revision)
        return self._newer_bundle_is_due(scan_id, consumed)

    def _mark_consumed_if_due(self, connection, scan_id, consumed) -> None:
        """Mark the bundle this job consumed, and only that bundle, as processed.

        A bundle that advanced while the job ran is left untouched; its own job
        marks it. This is what keeps a late upload from being reported complete
        by an older job's exit.
        """
        if consumed is None:
            return
        version, manifest_hash = consumed
        with_connection = connection.execute(
            "SELECT complete, manifest_hash FROM evidence_bundles WHERE scan_id = ? AND version = ?",
            (str(scan_id), version),
        ).fetchone()
        if with_connection is None or not with_connection["complete"]:
            return
        repo.bundle_processed(connection, scan_id, version, manifest_hash)

    def _newer_bundle_is_due(self, scan_id, consumed) -> bool:
        """True when evidence closed over newer inputs while this job ran."""
        if consumed is None:
            return False
        with self.database.connect() as connection:
            latest = repo.latest_bundle(connection, scan_id)
        return latest is not None and (latest.version, latest.manifest_hash) != consumed

    def _assess(self, scan_id: uuid.UUID, revision: int, job=None) -> bool:
        with self.database.transaction() as connection:
            repo.set_state(connection, scan_id, "checking")
            graph = repo.graph_of(repo.require_revision(connection, scan_id, revision))
            scenario = repo.get_scenario(connection, scan_id)
        assessment = self.stages.assess(graph, scenario, pass_number=revision + 1)
        self._checkpoint()
        with self.database.transaction() as connection:
            repo.save_assessment(connection, assessment)
        with self.database.transaction() as connection:
            repo.set_state(connection, scan_id, "ready")
            # A fresh assessment has fresh finding ids, so the stills drawn for
            # the last one no longer belong to anything. Queueing this again
            # rather than once means a re-check redraws them.
            repo.queue_job_again(connection, scan_id, DISPLAY, revision)
        self._tell_results_ready(scan_id)
        maybe_queue_texture(self.database, self.store, self, scan_id, revision)
        self._maybe_queue_deep_simulation(scan_id, revision, scenario is not None)
        self.wake()
        return False

    def _maybe_queue_deep_simulation(self, scan_id: uuid.UUID, revision: int, has_scenario: bool) -> None:
        if not self.settings.auto_deep_simulation or not has_scenario:
            return
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM simulations WHERE scan_id=? AND revision=?",
                (str(scan_id), revision),
            ).fetchone()
        if existing is not None:
            return
        try:
            request = SimulationRequest(
                base_revision=revision,
                samples=self.settings.auto_deep_samples,
                router="typesafe",
                refine_with_astra=True,
                typesafe_call_limit=self.settings.auto_deep_typesafe_call_limit,
                astra_rounds=self.settings.auto_deep_astra_rounds,
                exhaustive_evaluations=self.settings.auto_deep_exhaustive_evaluations,
            )
            queue_simulation(self.database, self.stages, self, scan_id, request)
        except ApiProblem as error:
            log.warning(
                "automatic deep simulation skipped for %s revision %s: %s",
                scan_id,
                revision,
                error.body.error,
            )
        except ValueError:
            log.warning(
                "automatic deep simulation skipped for %s revision %s: invalid limits",
                scan_id,
                revision,
            )

    def _display(self, scan_id: uuid.UUID, revision: int, job=None) -> bool:
        with self.database.connect() as connection:
            revision_row = repo.require_revision(connection, scan_id, revision)
            graph = repo.graph_of(revision_row)
            has_glb = revision_row["glb_path"] is not None
            assessment = repo.assessment_for_revision(connection, scan_id, revision)
            usdz = repo.artifact_of_kind(connection, scan_id, "room_usdz")
            mapping = repo.artifact_of_kind(connection, scan_id, "room_metadata")
            lidar = repo.artifact_of_kind(connection, scan_id, "lidar_mesh")
        revision_dir = self.store.scan_dir(scan_id) / "revisions" / str(revision)
        if not has_glb:
            lidar_path = self.store.artifact_path(scan_id, lidar.id) if lidar is not None else None
            self._store_geometry(scan_id, graph, revision_dir, usdz, mapping, lidar_path)
        if assessment is not None:
            self._checkpoint()
            rendered = self.stages.renders(
                graph,
                assessment,
                revision_dir / "renders",
                lambda finding_id: f"/api/scans/{scan_id}/renders/{finding_id}.png",
            )
            self._checkpoint()
            with self.database.transaction() as connection:
                repo.save_assessment(connection, rendered)
        return False

    def _store_geometry(self, scan_id, graph, revision_dir, usdz, mapping, lidar_mesh=None) -> None:
        inputs = revision_dir / "inputs"
        usdz_path = self._named_input(scan_id, usdz, inputs, "room.usdz")
        mapping_path = self._named_input(scan_id, mapping, inputs, self._mapping_name(scan_id, mapping))
        glb = self.stages.geometry(graph, revision_dir / "scene.glb", usdz_path, mapping_path, lidar_mesh)
        if glb is not None:
            with self.database.transaction() as connection:
                repo.set_glb_path(connection, scan_id, graph.revision, str(glb))

    def _named_input(self, scan_id, artifact, directory, name):
        """Blender's USD importer reads the file extension, and uploads are stored by artifact ID."""
        if artifact is None:
            return None
        directory.mkdir(parents=True, exist_ok=True)
        link = directory / name
        link.unlink(missing_ok=True)
        link.symlink_to(self.store.artifact_path(scan_id, artifact.id))
        return link

    def _mapping_name(self, scan_id, mapping) -> str:
        if mapping is None:
            return "room.metadata"
        head = self.store.artifact_path(scan_id, mapping.id).read_bytes()[:8]
        return "room.metadata.plist" if head.startswith(b"bplist") else "room.metadata.json"


class ChildTimedOut(RuntimeError):
    """A child ran past its time limit and was killed."""


class ChildFailed(RuntimeError):
    """A function run in its own process raised. What it raised crosses back as text, with
    whether it goes away by itself, because an exception's cause and context don't pickle."""

    def __init__(self, description: str, transient: bool = False):
        super().__init__(description)
        self.transient = transient


@dataclass(frozen=True)
class _ChildReport:
    value: object = None
    failure: str | None = None
    transient: bool = False


def in_own_process(function: Callable[..., object], *args: object, timeout_seconds: float | None = None) -> object:
    """Run a module-level function in a fresh interpreter, wait, and return what it returned.

    A child still running after `timeout_seconds` is killed along with every
    process it started, such as a Blender run, so one hung stage can't hold its
    loop, and every job queued behind it, for ever. What the function raised is
    raised here as `ChildFailed`; a child that died without a word, as `RuntimeError`.
    """
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    child = context.Process(target=_report_to_parent, args=(sender, function, *args), daemon=True)
    child.start()
    sender.close()
    try:
        child.join(timeout_seconds)
        if child.is_alive():
            _kill_with_everything_it_started(child)
            raise ChildTimedOut(
                f"{function.__name__} did not finish within {_duration(timeout_seconds or 0)} and was stopped"
            )
        report = _received(receiver)
    finally:
        receiver.close()
    return _outcome(function, child.exitcode, report)


def _report_to_parent(sender, function: Callable[..., object], *args: object) -> None:
    """The child's side: lead a process group of its own, run the function, and send back how it went."""
    os.setpgrp()
    logging.basicConfig(level=logging.INFO)
    try:
        report = _ChildReport(value=function(*args))
    except Exception as error:
        log.exception("%s failed in its own process", function.__name__)
        report = _ChildReport(
            failure=f"{type(error).__name__}: {error}"[:LONGEST_CHILD_ERROR], transient=is_transient(error)
        )
    sender.send(report)
    sender.close()


def _kill_with_everything_it_started(child) -> None:
    """Kill the child's process group, or only the child when it was killed before it could lead one."""
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        child.kill()
    child.join()


def _received(receiver) -> _ChildReport | None:
    if not receiver.poll():
        return None
    try:
        return receiver.recv()
    except EOFError:
        return None


def _outcome(function: Callable[..., object], exitcode: int | None, report: _ChildReport | None) -> object:
    if report is not None and report.failure is not None:
        raise ChildFailed(report.failure, report.transient)
    if exitcode != 0 or report is None:
        raise RuntimeError(f"{function.__name__} exited with code {exitcode}")
    return report.value


def _duration(seconds: float) -> str:
    if seconds >= 60 and seconds % 60 == 0:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'' if minutes == 1 else 's'}"
    return f"{seconds:g} second{'' if seconds == 1 else 's'}"


def _job_error(kind: str, error: Exception) -> str:
    """What the job row says went wrong. A simulation's own errors can carry a provider's payload,
    so only its deadline is spelled out."""
    if isinstance(error, JobOverran):
        return str(error)
    if kind == SIMULATE:
        return "Simulation failed; check the server log and retry"
    if isinstance(error, ChildFailed):
        return str(error)
    return f"{type(error).__name__}: {error}"


def is_transient(error: BaseException) -> bool:
    """Whether this error, or any error it was raised from, goes away by itself."""
    seen: set[int] = set()
    link: BaseException | None = error
    while link is not None and id(link) not in seen:
        if _transient_alone(link):
            return True
        seen.add(id(link))
        link = link.__cause__ or link.__context__
    return False


def _is_lock_contention(error: sqlite3.OperationalError) -> bool:
    """Another writer holds the database past its busy timeout, which ends when that writer commits."""
    return "database is locked" in str(error) or "database is busy" in str(error)


def _unsettled_problem(job_id: int, error: str) -> str:
    return (
        f"how job {job_id} ended could not be recorded ({error}); it stays running until"
        " the API restarts and queues it again, so fix the database, then restart"
    )


def _transient_alone(error: BaseException) -> bool:
    if isinstance(error, ChildFailed):
        return error.transient
    if isinstance(error, sqlite3.OperationalError):
        return _is_lock_contention(error)
    if isinstance(error, urllib.error.URLError):
        return isinstance(error.reason, TimeoutError)
    return isinstance(error, TimeoutError)


def bake_photos(settings: Settings, scan_id: uuid.UUID, build_id: int) -> None:
    """One photo build, run where its arithmetic cannot hold up the API's requests."""
    run_texture(Database(settings.database_path), _store_for(settings), Stages(), scan_id, build_id)


def run_job(settings: Settings, stages_for: Callable[[Settings], Stages], job: dict) -> bool:
    """One process, assess, display or simulate job, run in a process the worker can kill at its deadline.

    The child opens its own database connections and store from the settings
    and runs the same stage the worker thread would, on a worker that never
    starts its loops, so the results land exactly where an in-thread run puts them.
    """
    worker = Worker(Database(settings.database_path), _store_for(settings), stages_for(settings), settings)
    worker.notifier = notifier_from(settings)
    return worker.run_stage(job)


def _store_for(settings: Settings) -> ArtifactStore:
    quota = ScanQuota(settings.max_scan_artifacts, settings.max_scan_bytes)
    return ArtifactStore(settings.data_dir, settings.max_artifact_bytes, quota)
