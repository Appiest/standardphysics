"""Paint the four Moffett room captures and publish them as one library floor.

The painting lives in `standardphysics_pipeline.textures.library_scan` and the publishing
lives in `standardphysics_api.textures`. This file only says which captures make up the
floor, where the alignment came from, and which scan they belong to.

Two limits recorded in the transforms file carry straight through to what you see. The
room-to-room registration residual is around four to five centimetres, and bottom_left is
tied on under a metre of overlap, so surfaces near a room boundary can appear doubled. The
rooms are concatenated rather than welded, so an overlapped stretch of floor is kept twice.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import uuid

import numpy as np
from align_moffett_floor import DEFAULT_FLOOR_ORIGIN_CORRECTIONS_M
from standardphysics_contracts import SceneGraph, TextureBuild, TextureCoverage, graph_hash
from standardphysics_pipeline.discovery.discover import detections_digest
from standardphysics_pipeline.ingest import capture_to_room_from_payload, parse_room_json
from standardphysics_pipeline.textures.library_scan import RoomCapture, paint_the_rooms

from standardphysics_api.db import Database
from standardphysics_api.store import ArtifactStore
from standardphysics_api.textures import build_dir, build_prefix, finish_build, record_build, staged_build_dir

SCAN_ID = uuid.UUID("f143082d-f529-494b-b80d-97729234e334")
DATA_DIR = pathlib.Path("services/api/var")
DATASETS_DIR = pathlib.Path("datasets/phone/moffett")
TRANSFORMS_PATH = pathlib.Path("runs/moffett/verified-four-room-transforms.json")
ENHANCEMENTS_DIR = DATA_DIR / "scans" / str(SCAN_ID) / "enhancements"

ROOMS = {
    "center": ("454B3661-D3F8-46E8-ADAA-3123E759AA64", 90),
    "top": ("BBB88E0A-A2C3-4611-AFE1-71C43454E8C6", 100),
    "bottom_left": ("D9946491-26FD-4864-9673-C88180328109", 80),
    "left": ("A92A8ED6-87A5-474D-8514-D2D0E6A1F00D", 90),
}


def room_capture(
    name: str, capture_id: str, max_photos: int, to_floor: np.ndarray, *,
    use_enhancements: bool = True,
) -> RoomCapture:
    directory = DATASETS_DIR / capture_id
    poses_path = directory / "poses.json"
    frames = directory / "frames"
    frame_paths = {
        pose["frame_id"]: frames / pathlib.Path(pose["image"]).name
        for pose in json.loads(poses_path.read_text())
        if pose.get("frame_id")
    }
    room_payload = json.loads((directory / "room.json").read_text())
    enhanced = ENHANCEMENTS_DIR / f"{name}-graph.json"
    graph = (
        SceneGraph.model_validate_json(enhanced.read_text())
        if use_enhancements and enhanced.is_file() else parse_room_json(room_payload)
    )
    return RoomCapture(
        name=name,
        mesh_path=directory / "lidar-mesh.json",
        poses_path=poses_path,
        frame_paths=frame_paths,
        capture_to_room=capture_to_room_from_payload(room_payload),
        to_floor=np.asarray(to_floor, dtype=np.float64),
        max_photos=max_photos,
        graph=graph,
        detections_dir=directory / "detections",
    )


def latest_graph(database: Database) -> SceneGraph:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT graph_json FROM revisions WHERE scan_id=? ORDER BY revision DESC LIMIT 1", (str(SCAN_ID),)
        ).fetchone()
    if row is None:
        raise RuntimeError(f"no revision stored for {SCAN_ID}")
    return SceneGraph.model_validate_json(row["graph_json"])


def newest_scene_glb(store: ArtifactStore) -> pathlib.Path | None:
    """The reconstructed furniture, which the viewer shows beside the painted floor."""
    revisions = store.scan_dir(SCAN_ID) / "revisions"
    candidates = sorted(
        (path for path in revisions.glob("*/scene.glb")),
        key=lambda path: int(path.parent.name),
        reverse=True,
    )
    return candidates[0] if candidates else None


def _paint_equivalent(previous: SceneGraph, current: SceneGraph) -> bool:
    old, new = previous.model_dump(mode="json"), current.model_dump(mode="json")
    for graph in (old, new):
        graph.pop("revision", None)
        graph.pop("base_hash", None)
        for node in graph["nodes"]:
            node.pop("label", None)
            node.pop("labeled_by", None)
    return old == new


def _reuse_painted_build(
    database: Database, store: ArtifactStore, graph: SceneGraph, inputs: dict, key: str,
) -> TextureBuild | None:
    destination = build_dir(store, SCAN_ID) / key
    with database.connect() as connection:
        row = connection.execute(
            "SELECT graph_json, inputs_json, result_json FROM texture_builds WHERE scan_id=? AND build_key=?",
            (str(SCAN_ID), key),
        ).fetchone()
    if row is None:
        if destination.exists():
            raise RuntimeError(f"unregistered texture build already exists: {destination}")
        return None
    if row["result_json"] is None or not (destination / "scan.glb").is_file():
        raise RuntimeError(f"registered texture build is incomplete: {destination}")
    previous = SceneGraph.model_validate_json(row["graph_json"])
    if json.loads(row["inputs_json"]) != inputs or not _paint_equivalent(previous, graph):
        raise RuntimeError("matching texture key has different paint inputs or geometry")
    result = TextureBuild.model_validate_json(row["result_json"]).model_copy(update={"bake_graph": graph})
    record_build(database, SCAN_ID, key, graph, inputs, result)
    temporary = destination / ".result.json.tmp"
    temporary.write_text(result.model_dump_json())
    temporary.replace(destination / "result.json")
    return result


def published(database: Database, store: ArtifactStore, graph: SceneGraph, rooms: list[RoomCapture], inputs: dict) -> str:
    """Paint into a staging directory and move it into place only once it is whole."""
    key = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    reused = _reuse_painted_build(database, store, graph, inputs, key)
    if reused is not None:
        return reused.scan_glb_url
    staged = staged_build_dir(store, SCAN_ID)
    try:
        painted = paint_the_rooms(rooms, staged / "scan.glb")
        print(f"{painted.vertices:,} vertices, {painted.triangles:,} triangles from {painted.photos_used} photos")
        print(f"directly observed by a camera: {painted.directly_observed_fraction:.1%}")
        scene_glb = newest_scene_glb(store)
        if scene_glb is not None:
            shutil.copyfile(scene_glb, staged / "scene.glb")
        prefix = build_prefix(SCAN_ID, key)
        result = TextureBuild(
            build_id=key,
            glb_url=prefix + "/scene.glb",
            scan_glb_url=prefix + "/scan.glb",
            coverage_mask_urls=[],
            bake_graph=graph,
            coverage=TextureCoverage(
                textured_fraction=painted.directly_observed_fraction, nodes=[], needs_another_view=[],
            ),
            frames_used=painted.photos_used,
            seconds=painted.seconds,
        )
        finish_build(staged, build_dir(store, SCAN_ID) / key, result)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    record_build(database, SCAN_ID, key, graph, inputs, result)
    return result.scan_glb_url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR)
    parser.add_argument("--transforms", type=pathlib.Path, default=TRANSFORMS_PATH)
    args = parser.parse_args()

    to_floor = json.loads(args.transforms.read_text())["room_transforms"]
    rooms = []
    for name, (capture, photos) in ROOMS.items():
        matrix = np.asarray(to_floor[name], dtype=np.float64)
        correction = DEFAULT_FLOOR_ORIGIN_CORRECTIONS_M["bottom" if name == "bottom_left" else name]
        matrix[2, 3] += correction
        rooms.append(room_capture(name, capture, photos, matrix))

    database = Database(args.data_dir / "standardphysics.sqlite3")
    # max_bytes caps uploads, and this script only reads paths out of the store.
    store = ArtifactStore(args.data_dir, max_bytes=0)
    inputs = {
        "rooms": sorted(ROOMS),
        "transforms_sha256": hashlib.sha256(args.transforms.read_bytes()).hexdigest(),
        "room_graph_hashes": {room.name: graph_hash(room.graph) for room in rooms if room.graph is not None},
        "detections": {room.name: detections_digest(room.detections_dir) for room in rooms if room.detections_dir is not None},
        "pipeline": "patched-library-v1",
    }
    print(f"published {published(database, store, latest_graph(database), rooms, inputs)}")


if __name__ == "__main__":
    main()
