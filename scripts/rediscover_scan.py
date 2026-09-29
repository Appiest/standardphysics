"""Run discovery again on a stored scan and save what it finds as the scan's next revision.

Discovery keeps improving, and a scan uploaded before an improvement keeps what
the older code found. This runs it again from the stored photos, reading every
answer the model already gave from the scan's detection cache, and saves the
result on top of the latest revision. Every node RoomPlan measured is kept as
it stands in that revision, relabelled or moved; everything else came from the
photos, and discovery makes all of it again. Picking what to drop by label is
not enough: a whiteboard hung on a discovered partition is labelled like a
RoomPlan node, and keeping it would leave it pointing at a partition that no
longer exists.

    python scripts/rediscover_scan.py --scan <uuid> [--db ...] [--scans ...]

The API's worker assesses the new revision and draws its display geometry.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "services" / "api"))

from standardphysics_contracts import SceneGraph  # noqa: E402

from standardphysics_api import (
    repository,  # noqa: E402
    repository_jobs,  # noqa: E402
    repository_revisions,  # noqa: E402
)
from standardphysics_api.combine import captured_graph  # noqa: E402
from standardphysics_api.db import Database  # noqa: E402
from standardphysics_api.stages import Stages  # noqa: E402
from standardphysics_api.store import ArtifactStore  # noqa: E402
from standardphysics_api.worker_handlers import ASSESS  # noqa: E402

UNREAD_LIMIT = 0.1
"""Share of photos the model may fail to read before the run is refused.

A cache written under another model name misses on every photo, and saving
that run would replace every discovered object with nothing."""


def _inputs(connection, store: ArtifactStore, scan_id: uuid.UUID):
    frames = repository.artifacts_of_kind(connection, scan_id, "frames")
    poses = repository.artifact_of_kind(connection, scan_id, "poses")
    lidar = repository.artifact_of_kind(connection, scan_id, "lidar_mesh")
    if poses is None or lidar is None or not frames:
        raise SystemExit("the scan has no photos, poses or LiDAR mesh to discover from")
    return (
        [store.artifact_path(scan_id, artifact.id) for artifact in frames],
        store.artifact_path(scan_id, poses.id),
        store.artifact_path(scan_id, lidar.id),
    )


def _measured_only(graph: SceneGraph, capture: SceneGraph) -> SceneGraph:
    measured = {node.id for node in capture.nodes}
    return graph.model_copy(update={"nodes": [node for node in graph.nodes if node.id in measured]})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", required=True)
    parser.add_argument("--db", type=pathlib.Path, default=pathlib.Path("services/api/var/standardphysics.sqlite3"))
    parser.add_argument("--scans", type=pathlib.Path, default=pathlib.Path("services/api/var/scans"))
    args = parser.parse_args()
    scan_id = uuid.UUID(args.scan)
    database = Database(args.db)
    store = ArtifactStore(args.scans.parent, max_bytes=0)
    with database.connect() as connection:
        latest = repository_revisions.graph_of(repository_revisions.get_revision(connection, scan_id, None))
        frame_paths, poses_path, lidar_mesh_path = _inputs(connection, store, scan_id)
    found, outcome = Stages().discover_scan_with_report(
        _measured_only(latest, captured_graph(store, scan_id)),
        frame_paths=frame_paths, poses_path=poses_path, lidar_mesh_path=lidar_mesh_path,
    )
    unread = len(outcome.failures)
    if unread > UNREAD_LIMIT * max(1, outcome.frames_read + unread):
        raise SystemExit(f"{unread} photos could not be read, so nothing was saved: {outcome.note()}")
    revised = SceneGraph.model_validate(
        found.model_copy(update={"revision": latest.revision + 1, "base_hash": repository.graph_hash(latest)}).model_dump()
    )
    with database.transaction() as connection:
        if repository_revisions.get_revision(connection, scan_id)["revision"] != latest.revision:
            raise SystemExit("the scan was saved again while discovery ran: run this once more")
        repository_revisions.save_revision(connection, revised, source="rediscover", base_revision=latest.revision)
        repository_jobs.enqueue_job(connection, scan_id, ASSESS, revised.revision)
    print(f"{scan_id}: revision {revised.revision}, {outcome.note()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
