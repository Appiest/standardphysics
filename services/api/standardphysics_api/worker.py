"""Background threads that run queued jobs in order, one per lane (`LOOP_NAMES`).

Run one API process per database. Before it touches the queue the worker takes
an exclusive lock on a file beside the database; a second process finds the
lock held, says so in the log, and serves requests without running any job.
Jobs are claimed atomically, and at startup every job left running is queued
again, except a simulation, which is failed so that a restart never spends a
second budget of paid model calls (`repo.fail_interrupted_simulations`), and a
job whose runs `max_job_interruptions` restarts in a row have cut short, which
is failed so that an input that kills the server can't bring it down for ever
(`repo.requeue_interrupted_jobs`). The lock is what makes that safe: no other
live process can be running one of them.

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
failed, and the loop moves on to the next job. A spawned child inherits none
of the parent's tracing, so each one starts Weave from the settings itself and
flushes it before it exits.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from standardphysics_agents.tracing import tracing_for_this_process

from . import drain as deploy_drain
from . import evidence, guest_sweep
from . import repository as repo
from .db import Database
from .furniture import FURNITURE
from .notifications import notifier_from
from .rearrangement import INTERRUPTED, REARRANGE, Rearranger, failure_text, scale_down_when_idle
from .settings import Settings
from .simulations import SIMULATE
from .stages import Stages, configured_stages
from .store import ArtifactStore
from .textures import TEXTURE
from .worker_child import ChildFailed, ChildTimedOut, in_own_process, is_lock_contention, is_transient
from .worker_handlers import ASSESS, DISPLAY, PROCESS, JobHandlers, store_for
from .worker_lock import WorkerLock
from .worker_pulse import PROBLEM_STATES, Backoff, JobOverran, LoopPulse, RunningJob

log = logging.getLogger(__name__)

SWEEP_SECONDS = 3600.0
IDLE_WAIT_SECONDS = 2.0
STANDBY_RETRY_SECONDS = 5.0
"""How often a process that found the queue taken tries the lock again."""
LOOP_NAMES: dict[bool | str, str] = {
    False: "jobs",
    True: "textures",
    REARRANGE: "rearrange",
    FURNITURE: "furniture",
    SIMULATE: "simulate",
}
"""Each worker loop by its lane: False takes every job but the laned kinds, True takes the Blender jobs
(texture bakes and display renders, one at a time, so a new scan's measuring never waits behind a render),
REARRANGE takes layout suggestions, whose provider calls can wait on a cold deployment, FURNITURE
takes furniture refinement, whose model trials run long after a scan's render is ready, and SIMULATE
takes simulations, which may run for hours and would otherwise hold every new scan's measuring
behind them. A simulation works on the graph saved on its own row and writes only that row, so it
is safe beside a check of the same scan. Display can finish beside a re-check of the same revision,
so it draws again when the findings it drew were replaced while it ran (`_render_until_current`)."""
MAX_CLAIMS_BEFORE_START = 3
"""How many times a job may be claimed and put back because of an error before it ran."""
SETTLE_PATIENCE_SECONDS = 60.0
"""How long writing a job's outcome waits out a database another writer holds before giving up."""
TRANSIENT_ATTEMPTS = 3
"""How many times a job runs when it keeps meeting an error that goes away by itself: a
database another writer holds past its busy timeout, or a provider request that timed out."""


@dataclass
class _JobOutcome:
    error: str | None = None
    follow_up: bool = False
    """Queue the next derived job after this one settles; its row is still running now."""



class Worker(JobHandlers):
    def __init__(
        self,
        database: Database,
        store: ArtifactStore,
        stages: Stages,
        settings: Settings,
        rearranger: Rearranger | None = None,
    ):
        super().__init__(database, store, stages, settings, rearranger)
        self._stop = threading.Event()
        self.pulses = {texture_only: LoopPulse() for texture_only in LOOP_NAMES}
        self.lock = WorkerLock(database.path)
        self._standby = False
        self._swept_at: float | None = None
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
                "another worker already holds %s (process %s), so this process serves requests and takes over"
                " the jobs when that process exits; run one API process per database",
                self.lock.path,
                self.lock.holder(),
            )
            threading.Thread(target=self._wait_for_the_lock, name="standardphysics-standby", daemon=True).start()
            return
        self._take_the_queue()

    def _wait_for_the_lock(self) -> None:
        """A deploy can overlap the old process for a moment; once it exits, this one runs the jobs."""
        while not self._stop.wait(STANDBY_RETRY_SECONDS):
            if self.lock.acquire():
                self._standby = False
                log.warning("the previous worker exited, so this process now runs the jobs")
                self._take_the_queue()
                return

    def recheck_stale_results(self) -> int:
        """Queue a check of every shop whose results older checks made; how many were queued."""
        with self.database.transaction() as connection:
            stale = repo.results_made_under_other_checks(connection)
            for scan_id, revision in stale:
                repo.queue_job_again(connection, scan_id, ASSESS, revision)
        if stale:
            log.warning("checking %d shops again: their results came from checks this deploy changed", len(stale))
        return len(stale)

    def _take_the_queue(self) -> None:
        try:
            self._recover_interrupted_jobs()
            self.recheck_stale_results()
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
            connection.execute(
                "UPDATE jobs SET state='failed', error=? WHERE kind=? AND state='running'", (INTERRUPTED, REARRANGE)
            )
            stopped = repo.requeue_interrupted_jobs(connection, self.settings.max_job_interruptions)
        for job in stopped:
            log.error(
                "job %s (%s, scan %s) was stopped after %s interrupted runs instead of being queued again",
                job["id"],
                job["kind"],
                job["scan_id"],
                job["interruptions"],
            )


    def stop(self) -> None:
        self.live_reader.stop()
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


    def drain(self) -> None:
        """Run every queued job on the calling thread. Tests use this."""
        while self.run_once():
            pass

    def run_once(self, texture_only: bool | None = None, kind: str | None = None) -> bool:
        if deploy_drain.is_draining(self.settings.data_dir):
            return False
        with self.database.transaction() as connection:
            job = repo.claim_job(connection, texture_only, kind=kind)
        if job is None:
            return False
        lane = kind if kind is not None else texture_only
        pulse = (self.pulses.get(lane) if lane is not None else None) or LoopPulse()
        running = RunningJob(job["kind"], job["id"], time.monotonic(), self.settings.job_deadline_seconds(job["kind"]))
        pulse.begin(running)
        self._on_this_thread.job = running
        try:
            self._settle(job)
        finally:
            self._on_this_thread.job = None
            pulse.end()
        return True

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
        backoff = Backoff()
        give_up_at = time.monotonic() + SETTLE_PATIENCE_SECONDS
        while True:
            try:
                with self.database.transaction() as connection:
                    write(connection)
                return
            except sqlite3.OperationalError as error:
                delay = backoff.delay()
                if not is_lock_contention(error) or self._stop.is_set() or time.monotonic() + delay > give_up_at:
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

    def _loop(self, lane: bool | str = False) -> None:
        pulse = self.pulses[lane]
        backoff = Backoff()
        while not self._stop.is_set():
            pulse.beat()
            try:
                self._tick(lane)
            except Exception:
                delay = backoff.delay()
                log.error(
                    "worker loop %s failed; trying again in %.1f s:\n%s",
                    LOOP_NAMES[lane],
                    delay,
                    traceback.format_exc(),
                )
                self._stop.wait(delay)
                continue
            backoff.reset()

    def _tick(self, lane: bool | str) -> None:
        if self.run_once(*_claim_filter(lane)):
            return
        if lane == REARRANGE:
            scale_down_when_idle(self.database, self.rearranger)
        elif lane is False:
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
            if job["kind"] not in (DISPLAY, SIMULATE, TEXTURE, REARRANGE, FURNITURE):
                with self.database.transaction() as connection:
                    repo.set_state(connection, scan_id, "failed")
            return _JobOutcome(error=_job_error(job["kind"], exc))

    def _run_through_transient_errors(self, job, scan_id: uuid.UUID) -> bool:
        """Run the job's stage, and run it again after a short wait when it met an error that
        goes away by itself, up to TRANSIENT_ATTEMPTS runs in all and never past its deadline."""
        backoff = Backoff()
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


def _job_error(kind: str, error: Exception) -> str:
    """What the job row says went wrong. A simulation's own errors can carry a provider's payload,
    so only its deadline is spelled out."""
    if isinstance(error, JobOverran):
        return str(error)
    if kind == SIMULATE:
        return "Simulation failed; check the server log and retry"
    if kind == REARRANGE:
        return failure_text(error)
    if isinstance(error, ChildFailed):
        return str(error)
    return f"{type(error).__name__}: {error}"


def _claim_filter(lane: bool | str) -> tuple[bool | None, str | None]:
    """The `run_once` arguments that claim only this lane's jobs."""
    if isinstance(lane, str):
        return None, lane
    return lane, None


def _unsettled_problem(job_id: int, error: str) -> str:
    return (
        f"how job {job_id} ended could not be recorded ({error}); it stays running until"
        " the API restarts and queues it again, so fix the database, then restart"
    )


def run_job(settings: Settings, stages_for: Callable[[Settings], Stages], job: dict) -> bool:
    """One job other than a photo bake, run in a process the worker can kill at its deadline.

    The child opens its own database connections and store from the settings
    and runs the same stage the worker thread would, on a worker that never
    starts its loops, so the results land exactly where an in-thread run puts them.
    It traces to the same Weave project as the API, and sends its traces before it exits.
    """
    with tracing_for_this_process(settings.weave_project, settings.weave_entity):
        worker = Worker(Database(settings.database_path), store_for(settings), stages_for(settings), settings)
        worker.notifier = notifier_from(settings)
        return worker.run_stage(job)
