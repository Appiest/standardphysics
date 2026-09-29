"""The stage each job kind runs, on a worker thread or in a child process the worker spawned.

`JobHandlers` holds what every stage needs: the database, the store, the
stages, the settings and the in-memory state a stage boundary reads. `Worker`
builds on it with the loops that claim jobs and settle them. A child process
runs the same handlers through `run_job`, so a job's results land in the same
place wherever it ran.
"""

from __future__ import annotations

import json
import logging
import pathlib
import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime

from standardphysics_agents.tracing import tracing_for_this_process
from standardphysics_contracts import SimulationRequest
from standardphysics_pipeline.discovery.live import LiveReader, LiveReport
from standardphysics_pipeline.floor_coverage import with_floor_coverage

from . import evidence
from . import repository as repo
from .db import Database
from .errors import ApiProblem
from .furniture import FURNITURE, furniture_runtime, queue_furniture, run_furniture
from .notifications import LoggedNotifier, Notifier, Push
from .rearrangement import REARRANGE, Rearranger, run_suggestion
from .settings import Settings
from .simulations import SIMULATE, queue_simulation, run_simulation
from .stages import DiscoveryOutcome, Stages
from .store import ArtifactStore, ScanQuota
from .textures import TEXTURE, maybe_queue_texture, run_texture
from .worker_child import in_own_process
from .worker_pulse import RunningJob

log = logging.getLogger(__name__)

PROCESS, ASSESS, DISPLAY = "process", "assess", "display"


class _UnusableEvidence(Exception):
    """The evidence artifacts exist but do not agree with each other, so no
    semantic pass may consume them. Visible on the job and the evidence status;
    a new upload that repairs the pairing queues the next attempt."""


def _count_what_the_walk_read(outcome: DiscoveryOutcome, walk: LiveReport) -> None:
    """Put the photos read during the walk on the job record: they were real, billed requests."""
    outcome.read_during_walk = walk.read
    outcome.model_requests = [*walk.requests, *outcome.model_requests]


class JobHandlers:
    def __init__(
        self,
        database: Database,
        store: ArtifactStore,
        stages: Stages,
        settings: Settings,
        rearranger: Rearranger | None = None,
    ):
        self.database, self.store, self.stages = database, store, stages
        self.settings = settings
        self.rearranger = rearranger or Rearranger.from_settings(settings)
        self._wake = threading.Event()
        self.notifier: Notifier = LoggedNotifier()
        self._on_this_thread = threading.local()
        self.live_reader = LiveReader(read_photo=stages.read_photo)

    def wake(self) -> None:
        self._wake.set()

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

    def label_inputs(self, scan_id: uuid.UUID) -> tuple[list[pathlib.Path], pathlib.Path | None, pathlib.Path | None]:
        """Return uploaded frame and pose artifacts for the built-in labeler."""
        with self.database.connect() as connection:
            frames = repo.artifacts_of_kind(connection, scan_id, "frames")
            poses = repo.artifact_of_kind(connection, scan_id, "poses")
            lidar = repo.artifact_of_kind(connection, scan_id, "lidar_mesh")
        frame_paths = [self.store.artifact_path(scan_id, artifact.id) for artifact in frames]
        poses_path = self.store.artifact_path(scan_id, poses.id) if poses is not None else None
        return frame_paths, poses_path, self.store.artifact_path(scan_id, lidar.id) if lidar else None

    def handlers(self) -> dict[str, Callable[..., bool]]:
        """Each job kind this worker runs, with the stage that runs it. Every kind needs a deadline
        in `Settings.job_deadline_seconds`."""
        return {
            PROCESS: self._process,
            ASSESS: self._assess,
            DISPLAY: self._display,
            SIMULATE: self._simulate,
            TEXTURE: self._texture,
            REARRANGE: self._rearrange,
            FURNITURE: self._furniture,
        }

    def run_stage(self, job) -> bool:
        """Run the job's stage here and say whether a follow-up process job may be due."""
        scan_id, revision = uuid.UUID(job["scan_id"]), job["revision"]
        handler = self.handlers()[job["kind"]]
        if job["kind"] in (TEXTURE, FURNITURE):
            return handler(scan_id=scan_id, build_id=revision, job=job)
        return handler(scan_id=scan_id, revision=revision, job=job)

    def _rearrange(self, scan_id: uuid.UUID, revision: int, job=None) -> bool:
        run_suggestion(self.database, self.rearranger, self.stages, scan_id, revision)
        return False

    def _texture(self, scan_id, build_id, job=None) -> bool:
        if self.settings.bake_in_own_process:
            in_own_process(
                bake_photos, self.settings, scan_id, build_id, timeout_seconds=self.settings.bake_timeout_seconds
            )
        else:
            run_texture(self.database, self.store, self.stages, scan_id, build_id)
        queue_furniture(self.database, self, scan_id, build_id)
        return False

    def _furniture(self, scan_id, build_id, job=None) -> bool:
        run_furniture(self.database, self.store, scan_id, build_id, furniture_runtime(self.settings))
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
        read_during_walk = self.live_reader.finish(scan_id)
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
        else:
            _count_what_the_walk_read(outcome, read_during_walk)
        self._checkpoint()
        graph = self._with_floor_coverage(scan_id, graph)
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

    def _with_floor_coverage(self, scan_id: uuid.UUID, graph):
        """The ingested graph with the floor its LiDAR mesh saw, measured once here.

        It is stored on revision 0 and named by the mesh artifact's hash. A
        scan without a readable mesh keeps an empty coverage list, which the
        rearranging constraints treat as `NO_FLOOR_MAP`.
        """
        with self.database.connect() as connection:
            mesh = repo.artifact_of_kind(connection, scan_id, "lidar_mesh")
        if mesh is None:
            return graph
        return with_floor_coverage(graph, self.store.artifact_path(scan_id, mesh.id), mesh.sha256)

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
            self._render_until_current(scan_id, revision, graph, assessment, revision_dir / "renders")
        return False

    def _render_until_current(self, scan_id, revision, graph, assessment, renders_dir) -> None:
        """Draw each finding's still and save them with the findings they were drawn for.

        A re-check can finish while the stills are drawn, since display runs on
        the Blender lane. It cannot queue display again while this job runs, so
        this job draws again for the newer findings instead of leaving them bare.
        """
        while True:
            self._checkpoint()
            rendered = self.stages.renders(
                graph, assessment, renders_dir, lambda finding_id: f"/api/scans/{scan_id}/renders/{finding_id}.png"
            )
            self._checkpoint()
            with self.database.transaction() as connection:
                latest = repo.assessment_for_revision(connection, scan_id, revision)
                if latest is None or latest.id == assessment.id:
                    repo.save_assessment(connection, rendered)
                    return
            assessment = latest

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


def bake_photos(settings: Settings, scan_id: uuid.UUID, build_id: int) -> None:
    """One photo build, run where its arithmetic cannot hold up the API's requests."""
    with tracing_for_this_process(settings.weave_project, settings.weave_entity):
        run_texture(Database(settings.database_path), store_for(settings), Stages(), scan_id, build_id)


def store_for(settings: Settings) -> ArtifactStore:
    quota = ScanQuota(settings.max_scan_artifacts, settings.max_scan_bytes)
    return ArtifactStore(settings.data_dir, settings.max_artifact_bytes, quota)
