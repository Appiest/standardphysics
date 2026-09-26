"""One background thread that runs queued jobs in order.

Run one API process per database. Jobs are claimed atomically, and at startup
every job left running is queued again, on the assumption that the process that
claimed it has stopped. A second process on the same database would rerun the
first process's jobs.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import pathlib
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from standardphysics_contracts import SimulationRequest

from . import evidence, guest_sweep
from . import repository as repo
from .db import Database
from .errors import ApiProblem
from .notifications import LoggedNotifier, Notifier, Push
from .settings import Settings
from .simulations import SIMULATE, queue_simulation, run_simulation
from .stages import DiscoveryOutcome, Stages
from .store import ArtifactStore
from .textures import TEXTURE, maybe_queue_texture, run_texture

log = logging.getLogger(__name__)

PROCESS, ASSESS, DISPLAY = "process", "assess", "display"


GUEST_SWEEP_SECONDS = 3600.0


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
        self._thread: threading.Thread | None = None
        self._texture_thread: threading.Thread | None = None
        self.notifier: Notifier = LoggedNotifier()
        self._guests_swept_at = 0.0

    def start(self) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE jobs SET state='failed', error='Simulation interrupted; start a new run to continue'"
                " WHERE kind=? AND state='running'",
                (SIMULATE,),
            )
            repo.requeue_interrupted_jobs(connection)
        self._thread = threading.Thread(target=self._loop, name="standardphysics-worker", daemon=True)
        self._thread.start()
        self._texture_thread = threading.Thread(
            target=self._loop,
            args=(True,),
            name="standardphysics-textures",
            daemon=True,
        )
        self._texture_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._texture_thread is not None:
            self._texture_thread.join(timeout=5)

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
        outcome = self._run(job)
        with self.database.transaction() as connection:
            repo.finish_job(connection, job["id"], outcome.error)
            repo.record_job_attempt(connection, job["id"], job["attempts"], uuid.UUID(job["scan_id"]))
        if outcome.follow_up and outcome.error is None:
            self._queue_follow_up_if_due(uuid.UUID(job["scan_id"]))
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
        while not self._stop.is_set():
            if self.run_once(texture_only):
                continue
            if not texture_only:
                self._sweep_due_settled()
                self._sweep_guests_hourly()
            self._wake.wait(timeout=2.0)
            self._wake.clear()

    def _sweep_guests_hourly(self) -> None:
        if time.monotonic() - self._guests_swept_at < GUEST_SWEEP_SECONDS:
            return
        self._guests_swept_at = time.monotonic()
        try:
            guest_sweep.sweep(self.database, self.store, self.notifier, datetime.now(UTC))
        except Exception:
            log.warning("guest sweep failed:\n%s", traceback.format_exc())

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
        scan_id, revision = uuid.UUID(job["scan_id"]), job["revision"]
        handler = {
            PROCESS: self._process,
            ASSESS: self._assess,
            DISPLAY: self._display,
            SIMULATE: self._simulate,
            TEXTURE: self._texture,
        }[job["kind"]]
        try:
            if job["kind"] == TEXTURE:
                follow_up = handler(scan_id=scan_id, build_id=revision, job=job)
            else:
                follow_up = handler(scan_id=scan_id, revision=revision, job=job)
            return _JobOutcome(follow_up=follow_up)
        except Exception as exc:
            log.error("job %s %s failed:\n%s", job["kind"], scan_id, traceback.format_exc())
            if job["kind"] not in (DISPLAY, SIMULATE, TEXTURE):
                with self.database.transaction() as connection:
                    repo.set_state(connection, scan_id, "failed")
            if job["kind"] == SIMULATE:
                return _JobOutcome(error="Simulation failed; check the server log and retry")
            return _JobOutcome(error=f"{type(exc).__name__}: {exc}")

    def _texture(self, scan_id, build_id, job=None) -> bool:
        if self.settings.bake_in_own_process:
            in_own_process(bake_photos, self.settings, scan_id, build_id)
        else:
            run_texture(self.database, self.store, self.stages, scan_id, build_id)
        return False

    def _simulate(self, scan_id: uuid.UUID, revision: int, job=None) -> bool:
        run_simulation(self.database, self.store, self.stages, scan_id, revision)
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
            graph = repo.graph_of(repo.get_revision(connection, scan_id, revision))
            scenario = repo.get_scenario(connection, scan_id)
        assessment = self.stages.assess(graph, scenario, pass_number=revision + 1)
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
            graph = repo.graph_of(repo.get_revision(connection, scan_id, revision))
            has_glb = repo.get_revision(connection, scan_id, revision)["glb_path"] is not None
            assessment = repo.assessment_for_revision(connection, scan_id, revision)
            usdz = repo.artifact_of_kind(connection, scan_id, "room_usdz")
            mapping = repo.artifact_of_kind(connection, scan_id, "room_metadata")
        revision_dir = self.store.scan_dir(scan_id) / "revisions" / str(revision)
        if not has_glb:
            self._store_geometry(scan_id, graph, revision_dir, usdz, mapping)
        if assessment is not None:
            rendered = self.stages.renders(
                graph,
                assessment,
                revision_dir / "renders",
                lambda finding_id: f"/api/scans/{scan_id}/renders/{finding_id}.png",
            )
            with self.database.transaction() as connection:
                repo.save_assessment(connection, rendered)
        return False

    def _store_geometry(self, scan_id, graph, revision_dir, usdz, mapping) -> None:
        inputs = revision_dir / "inputs"
        usdz_path = self._named_input(scan_id, usdz, inputs, "room.usdz")
        mapping_path = self._named_input(scan_id, mapping, inputs, self._mapping_name(scan_id, mapping))
        glb = self.stages.geometry(graph, revision_dir / "scene.glb", usdz_path, mapping_path)
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


def in_own_process(function, *args) -> None:
    """Run a module-level function in a fresh interpreter and wait, raising if it did not finish cleanly."""
    child = multiprocessing.get_context("spawn").Process(target=function, args=args, daemon=True)
    child.start()
    child.join()
    if child.exitcode != 0:
        raise RuntimeError(f"{function.__name__} exited with code {child.exitcode}")


def bake_photos(settings: Settings, scan_id: uuid.UUID, build_id: int) -> None:
    """One photo build, run where its arithmetic cannot hold up the API's requests."""
    logging.basicConfig(level=logging.INFO)
    store = ArtifactStore(settings.data_dir, settings.max_artifact_bytes)
    run_texture(Database(settings.database_path), store, Stages(), scan_id, build_id)
