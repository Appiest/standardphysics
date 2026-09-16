"""Publish completed local recordings and serve them against their original revision.

    python -m standardphysics_api.replays runs/bread-test/bread-test-dual-camera.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI
from fastapi import Path as PathParameter
from fastapi.responses import FileResponse
from standardphysics_contracts import SimulationReplay, graph_hash

from . import repository as repo
from .db import Database
from .errors import ApiProblem
from .settings import Settings
from .store import ArtifactStore


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _directory(store: ArtifactStore, scan_id: UUID, revision: int) -> Path:
    path = (store.scan_dir(scan_id) / "revisions" / str(revision) / "replay").resolve()
    if revision < 0 or not path.is_relative_to(store.root):
        raise ApiProblem(400, "invalid replay path")
    return path


def _check_revision(database: Database, replay: SimulationReplay) -> None:
    with database.connect() as connection:
        row = repo.get_revision(connection, replay.scan_id, replay.revision)
    if row is None:
        raise ApiProblem(404, "no recorded revision")
    if graph_hash(repo.graph_of(row)) != replay.graph_hash:
        raise ApiProblem(409, "recording does not match the stored room")


def _manifest(source: Path) -> tuple[SimulationReplay, Path]:
    manifest = json.loads(source.read_text())
    report_path = Path(manifest["report"])
    if _sha256(report_path) != manifest["report_sha256"]:
        raise ValueError("The campaign changed after this video was rendered")
    report = json.loads(report_path.read_text())
    if report["complete"] is not True or report["evaluations"] != report["requested_evaluations"]:
        raise ValueError("Only completed campaigns can be published")
    if report["graph_hash"] != manifest["graph_hash"]:
        raise ValueError("The video and campaign use different room snapshots")
    video = Path(manifest["video"])
    replay = SimulationReplay(
        scan_id=report["scan_id"], revision=report["revision"], graph_hash=report["graph_hash"],
        report_sha256=manifest["report_sha256"], video_sha256=_sha256(video),
        evaluations=report["evaluations"], unique_layouts=report["unique_layouts"],
        connectivity_builds=report["connectivity_builds"], typesafe_calls=report["model_calls"],
        task_source=report["task_generation"]["source"], duration_seconds=manifest["duration_seconds"],
        selection=manifest["selection"], chapters=manifest["chapters"], limitations=report["limitations"],
    )
    _check_chapters(replay, report["recorded_runs"])
    return replay, video


def _check_chapters(replay: SimulationReplay, recordings: list[dict]) -> None:
    expected = [(run["task"]["title"], run["evaluation"], run["outcome"]) for run in recordings]
    actual = [(chapter.task, chapter.evaluation, chapter.outcome) for chapter in replay.chapters]
    times = [chapter.seconds for chapter in replay.chapters]
    if actual != expected or times != sorted(set(times)) or times[-1] >= replay.duration_seconds:
        raise ValueError("Video chapters must match the recorded campaign in order")


def publish(source: Path, database: Database, store: ArtifactStore) -> SimulationReplay:
    replay, video = _manifest(source)
    _check_revision(database, replay)
    destination = _directory(store, replay.scan_id, replay.revision)
    if destination.exists():
        raise ValueError("This revision already has a recording; existing evidence was preserved")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".replay-", dir=destination.parent))
    try:
        shutil.copyfile(video, staged / "video.mp4")
        if _sha256(staged / "video.mp4") != replay.video_sha256:
            raise ValueError("The video changed while being published")
        (staged / "manifest.json").write_text(replay.model_dump_json(indent=2))
        os.rename(staged, destination)
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    return replay


def _load(database: Database, store: ArtifactStore, scan_id: UUID, revision: int) -> SimulationReplay:
    path = _directory(store, scan_id, revision) / "manifest.json"
    if not path.is_file():
        raise ApiProblem(404, "no recording for this revision")
    replay = SimulationReplay.model_validate_json(path.read_text())
    if replay.scan_id != scan_id or replay.revision != revision:
        raise ApiProblem(409, "recording belongs to another room revision")
    _check_revision(database, replay)
    return replay


def install_replay_routes(app: FastAPI, database: Database, store: ArtifactStore) -> None:
    route = "/api/scans/{scan_id}/revisions/{revision}/replay"

    @app.get(route, response_model=SimulationReplay)
    def replay(scan_id: UUID, revision: Annotated[int, PathParameter(ge=0)]) -> SimulationReplay:
        return _load(database, store, scan_id, revision)

    @app.head(route + "/{asset}")
    @app.get(route + "/{asset}")
    def asset(
        scan_id: UUID, revision: Annotated[int, PathParameter(ge=0)], asset: Literal["video.mp4"]
    ) -> FileResponse:
        _load(database, store, scan_id, revision)
        path = _directory(store, scan_id, revision) / asset
        if not path.is_file():
            raise ApiProblem(404, "recording file is missing")
        return FileResponse(path, media_type="video/mp4")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    settings = Settings.from_environment()
    store = ArtifactStore(settings.data_dir, settings.max_artifact_bytes)
    replay = publish(args.manifest, Database(settings.database_path), store)
    print(f"Published {len(replay.chapters)} recorded runs for {replay.scan_id} revision {replay.revision}")


if __name__ == "__main__":
    main()
