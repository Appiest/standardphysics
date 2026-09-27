"""Write a placed floor out as one phone capture, so any server can take it through the phone's own upload.

`build_full_floor.py` makes the merged floor inside this machine's database. A
server somewhere else cannot be reached that way, but it accepts captures, so
this writes the placed walks as the directory the phone would have uploaded:
one RoomPlan export, one LiDAR mesh, one set of camera poses and every photo,
each walk moved by the placement its owner saved. `import_capture.py` then
uploads it and the server builds the floor exactly as it builds a capture.

RoomPlan's own archive of the session (`coreModel`) cannot be merged and is
left out. Nothing reads it.

The mesh can be thinned on the way out, because a server parses the whole mesh
at once: four walks of a library floor are four hundred megabytes of JSON, which
a small server cannot hold in memory. Snapping vertices to a grid a few
centimetres across keeps every surface where it was and drops only the detail
the phone measured finer than that.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import uuid

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from build_full_floor import SHARED_FLOOR, Walk, _latest_graph, _mesh_parts, _moved, _poses, _walk  # noqa: E402
from standardphysics_pipeline.blender import _run  # noqa: E402

from standardphysics_api.db import Database  # noqa: E402
from standardphysics_api.store import ArtifactStore  # noqa: E402

ROOM_ELEMENTS = ("walls", "doors", "windows", "openings", "floors", "objects")
MILLIMETRE_DECIMALS = 4
"""Thinned vertices are written to a tenth of a millimetre, which halves the file against full float precision."""
IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def _room_payload(store: ArtifactStore, walks: list[Walk]) -> dict:
    """Every walk's RoomPlan elements in one export, each moved by its walk's placement."""
    merged: dict = {key: [] for key in ROOM_ELEMENTS}
    merged.update(sections=[], version=2, story=0, referenceOriginTransform=IDENTITY)
    for walk in walks:
        payload = json.loads(store.artifact_path(walk.scan_id, "room-json").read_text())
        for key in ROOM_ELEMENTS:
            for element in payload.get(key) or []:
                merged[key].append({**element, "transform": _moved(element["transform"], walk.arkit_motion)})
        for section in payload.get("sections") or []:
            centre = walk.arkit_motion @ np.append(np.asarray(section["center"], dtype=np.float64), 1.0)
            merged["sections"].append({**section, "center": centre[:3].tolist()})
    return merged


def clustered(part: dict, cell: float) -> dict | None:
    """One mesh part with its vertices snapped to a grid `cell` metres across, or None if nothing is left."""
    vertices = np.asarray(part["vertices"], dtype=np.float64).reshape(-1, 3)
    triangles = np.asarray(part["triangles"], dtype=np.int64).reshape(-1, 3)
    cells, remap = np.unique(np.floor(vertices / cell).astype(np.int64), axis=0, return_inverse=True)
    remap = remap.ravel()
    sums = np.zeros((len(cells), 3))
    np.add.at(sums, remap, vertices)
    centres = sums / np.bincount(remap, minlength=len(cells))[:, None]
    snapped = remap[triangles]
    kept = snapped[(snapped[:, 0] != snapped[:, 1]) & (snapped[:, 1] != snapped[:, 2]) & (snapped[:, 0] != snapped[:, 2])]
    if len(kept) == 0:
        return None
    used, compact = np.unique(kept, return_inverse=True)
    return {**part, "vertices": np.round(centres[used], MILLIMETRE_DECIMALS).ravel().tolist(), "triangles": compact.ravel().tolist()}


def _mesh(store: ArtifactStore, walks: list[Walk], cell: float | None) -> dict:
    parts = [part for walk in walks for part in _mesh_parts(store, walk)]
    if cell:
        parts = [thinned for thinned in (clustered(part, cell) for part in parts) if thinned is not None]
    return {"floorY": 0.0, "parts": parts}


def _write_json(path: pathlib.Path, payload) -> str:
    data = json.dumps(payload, separators=(",", ":")).encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _photos(store: ArtifactStore, walks: list[Walk], poses: list[dict], out: pathlib.Path) -> list[dict]:
    """Every photo linked in under its new number, named as the phone names its uploads."""
    frames_dir = out / "frames"
    frames_dir.mkdir(exist_ok=True)
    entries = []
    for walk, source in _frame_sources(store, walks):
        frame_id = walk.frame_id(source.name)
        target = frames_dir / f"{frame_id.replace('-', '_')}.jpg"
        os.link(source, target)
        entries.append({"frame_id": frame_id, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "bytes": target.stat().st_size})
    posed = {record["frame_id"] for record in poses if record.get("frame_id")}
    return [entry for entry in entries if entry["frame_id"] in posed]


def _frame_sources(store: ArtifactStore, walks: list[Walk]):
    for walk in walks:
        for source in sorted(store.artifact_path(walk.scan_id, "poses").parent.glob("frame-*")):
            yield walk, source


def _usdz(store: ArtifactStore, walks: list[Walk], out: pathlib.Path) -> None:
    """The walks' RoomPlan models set on the floor and written as one usdz, which the server requires."""
    basis = np.asarray(SHARED_FLOOR.m, dtype=np.float64).reshape(4, 4).copy()
    basis[:3, 3] = 0.0
    command = ["--out", str(out)]
    for walk in walks:
        placement = basis @ walk.arkit_motion @ np.linalg.inv(basis)
        source = store.artifact_path(walk.scan_id, "room-usdz")
        copied = out.parent / f"walk-{walk.first_frame}.usdz"
        copied.write_bytes(source.read_bytes())
        command.extend(["--usdz", str(copied), "--placement", " ".join(f"{value:.9g}" for value in placement.ravel())])
    script = pathlib.Path(__file__).resolve().parent / "merge_room_usdz.py"
    output = _run(str(script), command)
    for walk in walks:
        (out.parent / f"walk-{walk.first_frame}.usdz").unlink()
    if "FLOOR_USDZ_WRITTEN" not in output:
        raise SystemExit(f"Blender did not write the floor's usdz:\n{output[-1500:]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--placed-scan", required=True, help="the merged scan whose walks were placed")
    parser.add_argument("--out", required=True, type=pathlib.Path, help="an empty directory to write the capture into")
    parser.add_argument("--name", required=True)
    parser.add_argument("--mesh-cell", type=float, default=None, help="thin the mesh to a grid this many metres across")
    parser.add_argument("--db", type=pathlib.Path, default=pathlib.Path("services/api/var/standardphysics.sqlite3"))
    parser.add_argument("--scans", type=pathlib.Path, default=pathlib.Path("services/api/var/scans"))
    args = parser.parse_args()

    placed_id = uuid.UUID(args.placed_scan)
    store = ArtifactStore(args.scans.parent, max_bytes=0)
    manifest = json.loads((store.scan_dir(placed_id) / "rooms.json").read_text())
    with Database(args.db).connect() as connection:
        placed = _latest_graph(connection, placed_id)
    walks = [_walk(store, room, placed, index) for index, room in enumerate(manifest["rooms"])]

    args.out.mkdir(parents=True, exist_ok=False)
    _write_json(args.out / "room.json", _room_payload(store, walks))
    mesh = _mesh(store, walks, args.mesh_cell)
    _write_json(args.out / "lidar-mesh.json", mesh)
    poses = [record for walk in walks for record in _poses(store, walk)]
    poses_sha = _write_json(args.out / "poses.json", poses)
    frames = _photos(store, walks, poses, args.out)
    _write_json(args.out / "photo-manifest.json", {"manifest_version": 1, "poses_sha256": poses_sha, "frames": frames})
    (args.out / "capture.json").write_text(json.dumps({"name": args.name}))
    _usdz(store, walks, args.out / "room.usdz")
    triangles = sum(len(part["triangles"]) // 3 for part in mesh["parts"])
    print(f"{args.name}: {len(poses)} cameras, {len(frames)} photos, {triangles:,} mesh triangles, into {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
