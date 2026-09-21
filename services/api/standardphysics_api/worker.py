"""One background thread that runs queued jobs in order.

Run one API process per database. Jobs are claimed atomically, and at startup
every job left running is queued again, on the assumption that the process that
claimed it has stopped. A second process on the same database would rerun the
first process's jobs.
"""

from __future__ import annotations

import logging
import pathlib
import threading
import traceback
import uuid

from standardphysics_contracts import SimulationRequest

from . import repository as repo
from .db import Database
from .errors import ApiProblem
from .settings import Settings
from .simulations import SIMULATE, queue_simulation, run_simulation
from .stages import Stages
from .store import ArtifactStore
from .textures import TEXTURE, maybe_queue_texture, run_texture

log = logging.getLogger(__name__)

PROCESS, ASSESS, DISPLAY = "process", "assess", "display"


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

    def start(self) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE jobs SET state='failed', error='Simulation interrupted; start a new run to continue'"
                " WHERE kind=? AND state='running'", (SIMULATE,),
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
        error = self._run(job)
        with self.database.transaction() as connection:
            repo.finish_job(connection, job["id"], error)
        return True

    def _loop(self, texture_only: bool = False) -> None:
        while not self._stop.is_set():
            if not self.run_once(texture_only):
                self._wake.wait(timeout=2.0)
                self._wake.clear()

    def _run(self, job) -> str | None:
        scan_id, revision = uuid.UUID(job["scan_id"]), job["revision"]
        handler = {
            PROCESS: self._process,
            ASSESS: self._assess,
            DISPLAY: self._display,
            SIMULATE: self._simulate,
            TEXTURE: self._texture,
        }[job["kind"]]
        try:
            handler(scan_id, revision)
            return None
        except Exception as exc:
            log.error("job %s %s failed:\n%s", job["kind"], scan_id, traceback.format_exc())
            if job["kind"] not in (DISPLAY, SIMULATE, TEXTURE):
                with self.database.transaction() as connection:
                    repo.set_state(connection, scan_id, "failed")
            if job["kind"] == SIMULATE:
                return "Simulation failed; check the server log and retry"
            return f"{type(exc).__name__}: {exc}"

    def _texture(self, scan_id, build_id):
        run_texture(self.database, self.store, self.stages, scan_id, build_id)

    def _simulate(self, scan_id: uuid.UUID, revision: int) -> None:
        run_simulation(self.database, self.store, self.stages, scan_id, revision)

    def _process(self, scan_id: uuid.UUID, revision: int) -> None:
        with self.database.connect() as connection:
            room_json = repo.artifact_of_kind(connection, scan_id, "room_json")
        frame_paths, poses_path, lidar_mesh_path = self.label_inputs(scan_id)
        graph = self.stages.ingest(
            self.store.artifact_path(scan_id, room_json.id),
            scan_id,
            frame_paths=frame_paths,
            poses_path=poses_path,
            lidar_mesh_path=lidar_mesh_path,
        )
        with self.database.transaction() as connection:
            repo.save_revision(connection, graph, source="ingest")
        self._assess(scan_id, graph.revision)

    def _assess(self, scan_id: uuid.UUID, revision: int) -> None:
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
        maybe_queue_texture(self.database, self.store, self, scan_id, revision)
        self._maybe_queue_deep_simulation(scan_id, revision, scenario is not None)
        self.wake()

    def _maybe_queue_deep_simulation(
        self, scan_id: uuid.UUID, revision: int, has_scenario: bool
    ) -> None:
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
            queue_simulation(
                self.database, self.stages, self, scan_id, request
            )
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

    def _display(self, scan_id: uuid.UUID, revision: int) -> None:
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
                graph, assessment, revision_dir / "renders",
                lambda finding_id: f"/api/scans/{scan_id}/renders/{finding_id}.png",
            )
            with self.database.transaction() as connection:
                repo.save_assessment(connection, rendered)

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
