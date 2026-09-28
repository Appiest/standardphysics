"""Turn a placed set of walks into one scan carrying all of their measurements.

`merge_captures.py` puts several walks of one floor into a single scan as boxes
and leaves them where they were captured. Somebody then drags each walk into
place in the workspace and saves, because the geometry cannot say how they fit:
a floor of parallel shelf rows correlates with itself at many angles.

This takes that saved placement and applies it to everything the boxes stood
for. Each walk was uploaded as a scan of its own, and the merged scan's
`rooms.json` names it. That scan's LiDAR mesh and camera poses are moved by the
same motion the owner gave its boxes, so the photographs keep pointing at the
surfaces they photographed. Its painted surface is moved the same way and
joined with the others, so the floor looks the way each walk already looked.

The boxes come from each walk as its own processing left them, not as its
phone measured them: processing removes and renames boxes as well as adding
them. Where two walks overlap and both boxed one thing, it is kept once.
`--regraph <floor>` redoes only that on a floor built before, as a new revision
of it that keeps every name the owner gave by hand, without touching its mesh,
photographs or painted surface.

The motion is recovered rather than trusted: every box's placed position is a
correspondence against where the phone measured it, and those go through the
registration fit, which refuses when they do not agree on one rigid motion.

The mesh and the poses are ARKit's, Y up, while the boxes are ours, Z up with
each walk's floor at zero. A turn on the floor plan is therefore a turn about
ARKit's Y, and each walk's own floor height has to come out on the way in.
Every walk leaves in one shared ARKit frame whose floor is at Y = 0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import sys
import uuid
from dataclasses import dataclass

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "services" / "api"))

from standardphysics_contracts import (  # noqa: E402
    Artifact,
    CreateScanRequest,
    SceneGraph,
    SceneNode,
    TextureBuild,
    TextureCoverage,
)
from standardphysics_pipeline.blender import export_glb  # noqa: E402
from standardphysics_pipeline.coords import capture_to_room  # noqa: E402
from standardphysics_pipeline.registration import PlaneAlignment  # noqa: E402
from standardphysics_pipeline.textures import texture_build_key  # noqa: E402
from standardphysics_pipeline.textures.library_scan import painted_scans_joined  # noqa: E402

from standardphysics_api import repository  # noqa: E402
from standardphysics_api.combine import (  # noqa: E402
    captured_graph,
    placement_matrix,
    placement_since_capture,
    walk_on_floor,
    without_repeats_across_walks,
)
from standardphysics_api.db import Database  # noqa: E402
from standardphysics_api.store import ArtifactStore  # noqa: E402
from standardphysics_api.textures import (  # noqa: E402
    bake_inputs,
    build_dir,
    build_prefix,
    finish_build,
    record_build,
    staged_build_dir,
)
from standardphysics_api.worker import ASSESS  # noqa: E402

FRAMES_PER_WALK = 10_000
"""Each walk's photos are renumbered into a block of their own, since a frame id is only digits."""

SHARED_FLOOR = capture_to_room(0.0)
"""The merged scan's own capture frame: ARKit's axes, with every walk's floor at Y = 0."""


@dataclass(frozen=True)
class Walk:
    name: str
    scan_id: uuid.UUID
    placement: PlaneAlignment
    capture_to_room: np.ndarray
    first_frame: int

    @property
    def to_floor(self) -> np.ndarray:
        """Row-major 4x4 in the room frame: the walk as measured to the walk as placed."""
        return placement_matrix(self.placement)

    @property
    def arkit_motion(self) -> np.ndarray:
        """Into the room frame, onto the floor, and back out to the shared ARKit frame."""
        shared = np.asarray(SHARED_FLOOR.m, dtype=np.float64).reshape(4, 4)
        return np.linalg.inv(shared) @ self.to_floor @ self.capture_to_room

    def frame_id(self, original: str) -> str:
        return f"frame-{self.first_frame + int(original.removeprefix('frame-')):05d}"


def _latest_graph(connection, scan_id: uuid.UUID) -> SceneGraph:
    row = repository.get_revision(connection, scan_id, None)
    if row is None:
        raise SystemExit(f"nothing saved on {scan_id}")
    return repository.graph_of(row)


def _walk(store: ArtifactStore, room: dict, placed: SceneGraph, index: int) -> Walk:
    if not room.get("source_scan_id"):
        raise SystemExit(f"the walk called {room['name']} does not say which scan it was uploaded as")
    scan_id = uuid.UUID(room["source_scan_id"])
    capture = captured_graph(store, scan_id)
    return Walk(
        name=room["name"],
        scan_id=scan_id,
        placement=placement_since_capture(capture, placed, room["node_ids"]),
        capture_to_room=np.asarray(capture.capture_to_room.m, dtype=np.float64).reshape(4, 4),
        first_frame=(index + 1) * FRAMES_PER_WALK,
    )


def _moved(columns: list[float], motion: np.ndarray) -> list[float]:
    """An ARKit column-major transform, carried by a row-major motion."""
    matrix = np.asarray(columns, dtype=np.float64).reshape(4, 4, order="F")
    return (motion @ matrix).reshape(16, order="F").tolist()


def _walk_nodes(connection, store: ArtifactStore, walk: Walk, room: dict, placed: SceneGraph) -> list[SceneNode]:
    """The walk as its own processing left it, moved to where the walk was placed."""
    capture = captured_graph(store, walk.scan_id)
    placed_ids = {node.id: uuid.UUID(node_id) for node, node_id in zip(capture.nodes, room["node_ids"])}
    latest = _latest_graph(connection, walk.scan_id)
    return walk_on_floor(latest, capture, placed, walk.to_floor, placed_ids, walk.frame_id)


def _joined(placed: SceneGraph, walk_nodes: list[list[SceneNode]], rooms: list[dict]) -> SceneGraph:
    """The placed scan with every walk's boxes replaced by the walk as processed, each thing once."""
    phone_boxes = {uuid.UUID(node_id) for room in rooms for node_id in room["node_ids"]}
    added_on_the_floor = [node for node in placed.nodes if node.id not in phone_boxes]
    return placed.model_copy(update={"nodes": [*added_on_the_floor, *without_repeats_across_walks(walk_nodes)]})


def _regraph(database: Database, floor_id: uuid.UUID, joined: SceneGraph, dry_run: bool) -> None:
    """A new revision of a floor built before, with the walks joined again and the owner's names kept."""
    with database.transaction() as connection:
        latest = _latest_graph(connection, floor_id)
        by_hand = {node.id: node for node in latest.nodes if node.labeled_by == "owner"}
        nodes = [by_hand.pop(node.id, node) for node in joined.nodes]
        graph = latest.model_copy(update={"revision": latest.revision + 1, "nodes": [*nodes, *by_hand.values()]})
        print(f"{floor_id}: {len(latest.nodes)} nodes at revision {latest.revision}, {len(graph.nodes)} in the next")
        if dry_run:
            return
        repository.save_revision(connection, graph, source="rebuild", base_revision=latest.revision)
        repository.enqueue_job(connection, floor_id, ASSESS, graph.revision)


def _mesh_parts(store: ArtifactStore, walk: Walk) -> list[dict]:
    mesh = json.loads(store.artifact_path(walk.scan_id, "lidar-mesh").read_text())
    for part in mesh["parts"]:
        part["transform"] = _moved(part["transform"], walk.arkit_motion)
    return mesh["parts"]


def _poses(store: ArtifactStore, walk: Walk) -> list[dict]:
    records = json.loads(store.artifact_path(walk.scan_id, "poses").read_text())
    for record in records:
        record["transform"] = _moved(record["transform"], walk.arkit_motion)
        if record.get("frame_id"):
            record["frame_id"] = walk.frame_id(record["frame_id"])
    return records


def _frames(connection, store: ArtifactStore, walk: Walk, scan_id: uuid.UUID) -> list[Artifact]:
    """Every photograph linked in under its new number, with the checksum it arrived with."""
    rows = connection.execute(
        "SELECT id, sha256, bytes FROM artifacts WHERE scan_id=? AND kind='frames'", (str(walk.scan_id),)
    ).fetchall()
    carried = []
    for row in rows:
        artifact = Artifact(id=walk.frame_id(row["id"]), kind="frames", sha256=row["sha256"], bytes=row["bytes"])
        os.link(store.artifact_path(walk.scan_id, row["id"]), store.artifact_path(scan_id, artifact.id))
        carried.append(artifact)
    return carried


def _written(store: ArtifactStore, scan_id: uuid.UUID, artifact_id: str, kind: str, payload) -> Artifact:
    data = json.dumps(payload, separators=(",", ":")).encode()
    store.artifact_path(scan_id, artifact_id).write_bytes(data)
    return Artifact(id=artifact_id, kind=kind, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))


def _painted_scan(connection, store: ArtifactStore, walk: Walk) -> pathlib.Path:
    """The newest painted surface the walk's own scan has, as the bake wrote it."""
    row = connection.execute(
        "SELECT result_json FROM texture_builds WHERE scan_id=? AND json_extract(result_json, '$.scan_glb_url')"
        " IS NOT NULL ORDER BY id DESC LIMIT 1",
        (str(walk.scan_id),),
    ).fetchone()
    if row is None:
        raise SystemExit(f"the walk called {walk.name} has never been painted: bake its own scan first")
    build = TextureBuild.model_validate_json(row["result_json"])
    return build_dir(store, walk.scan_id) / build.build_id / "scan.glb"


def _publish_floor(database: Database, store: ArtifactStore, scan_id: uuid.UUID, walks: list[Walk], painted: list[pathlib.Path]) -> str:
    """The walks' painted surfaces, placed and joined, published as the merged scan's photo build."""
    bake, inputs = bake_inputs(database, store, scan_id)
    if inputs is None:
        raise SystemExit("the merged scan's photographs did not register")
    key = texture_build_key(bake, inputs["digest"])
    staged = staged_build_dir(store, scan_id)
    try:
        painted_scans_joined([(path, walk.to_floor) for path, walk in zip(painted, walks)], staged / "scan.glb")
        export_glb(bake, staged / "scene.glb")
        prefix = build_prefix(scan_id, key)
        result = TextureBuild(
            build_id=key, glb_url=prefix + "/scene.glb", scan_glb_url=prefix + "/scan.glb",
            coverage_mask_urls=[], bake_graph=bake,
            coverage=TextureCoverage(textured_fraction=0.0, nodes=[], needs_another_view=[]),
            frames_used=len(inputs["frames"]), seconds=0.0,
        )
        finish_build(staged, build_dir(store, scan_id) / key, result)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    record_build(database, scan_id, key, bake, inputs, result)
    return result.scan_glb_url


def _insert_scan(database: Database, args: argparse.Namespace, scan_id: uuid.UUID, placed: SceneGraph, artifacts: list[Artifact]) -> None:
    graph = placed.model_copy(update={"scan_id": scan_id, "revision": 0, "capture_to_room": SHARED_FLOOR})
    with database.transaction() as connection:
        repository.insert_scan(
            connection,
            CreateScanRequest(name=args.name, device_model="merged floor", duration_seconds=0),
            uuid.UUID(args.owner),
            scan_id=scan_id,
            state="ready",
        )
        for artifact in artifacts:
            repository.insert_artifact(connection, scan_id, artifact)
        repository.save_revision(connection, graph, source="owner")
        repository.enqueue_job(connection, scan_id, ASSESS, 0)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--placed-scan", required=True, help="the merged scan whose walks were placed")
    parser.add_argument("--name", help="what to call a new floor")
    parser.add_argument("--owner", help="who owns a new floor")
    parser.add_argument("--regraph", help="a floor built before, to give the walks' current boxes as a new revision")
    parser.add_argument("--dry-run", action="store_true", help="with --regraph, say what would change and save nothing")
    parser.add_argument("--db", type=pathlib.Path, default=pathlib.Path("services/api/var/standardphysics.sqlite3"))
    parser.add_argument("--scans", type=pathlib.Path, default=pathlib.Path("services/api/var/scans"))
    args = parser.parse_args()
    if not args.regraph and not (args.name and args.owner):
        parser.error("a new floor needs --name and --owner")
    return args


def main() -> int:
    args = _parse_args()
    placed_id = uuid.UUID(args.placed_scan)
    manifest = json.loads((args.scans / str(placed_id) / "rooms.json").read_text())
    database = Database(args.db)
    store = ArtifactStore(args.scans.parent, max_bytes=0)
    with database.connect() as connection:
        placed = _latest_graph(connection, placed_id)
        if placed.revision == 0:
            raise SystemExit("that scan's walks have not been placed yet: align them and save first")
        walks = [_walk(store, room, placed, index) for index, room in enumerate(manifest["rooms"])]
        walk_nodes = [
            _walk_nodes(connection, store, walk, room, placed) for walk, room in zip(walks, manifest["rooms"])
        ]
        placed = _joined(placed, walk_nodes, manifest["rooms"])
    if args.regraph:
        _regraph(database, uuid.UUID(args.regraph), placed, args.dry_run)
        return 0
    with database.connect() as connection:
        painted = [_painted_scan(connection, store, walk) for walk in walks]

    scan_id = uuid.uuid4()
    store.artifact_path(scan_id, "poses").parent.mkdir(parents=True, exist_ok=True)
    parts, poses, frames = [], [], []
    with database.connect() as connection:
        for walk in walks:
            parts.extend(_mesh_parts(store, walk))
            poses.extend(_poses(store, walk))
            frames.extend(_frames(connection, store, walk, scan_id))
            print(f"  {walk.name:12} turned {np.degrees(walk.placement.yaw):8.2f}°, "
                  f"residual {walk.placement.residual_max * 1000:.1f} mm")
    artifacts = [
        _written(store, scan_id, "lidar-mesh", "lidar_mesh", {"floorY": 0.0, "parts": parts}),
        _written(store, scan_id, "poses", "poses", poses),
        *frames,
    ]
    _insert_scan(database, args, scan_id, placed, artifacts)
    print(f"\n{args.name}: {scan_id}")
    print(f"  {len(parts)} mesh parts, {len(poses)} cameras, {len(frames)} photographs, {len(placed.nodes)} boxes")
    print(f"  painted floor at {_publish_floor(database, store, scan_id, walks, painted)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
