"""Run SPAR3D on photographed A-102 furniture and publish passing display meshes.

The preserved captures have their own room frames. Generated meshes are measured
and judged there, then moved through the reviewed room transforms before they can
replace anything in the floor viewer. Rejected models never replace LiDAR.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import uuid

import numpy as np
import trimesh
from align_moffett_floor import DEFAULT_FLOOR_ORIGIN_CORRECTIONS_M
from bake_library_textures import DATA_DIR, ENHANCEMENTS_DIR, ROOMS, SCAN_ID, TRANSFORMS_PATH
from spar3d_furniture_experiment import run_evidence, source_room_evidence
from standardphysics_contracts import SceneGraph, TextureBuild

from standardphysics_api.db import Database
from standardphysics_api.furniture import _accepted_mesh, candidate_nodes, furniture_class, furniture_mesh_url
from standardphysics_api.store import ArtifactStore
from standardphysics_api.textures import build_dir, build_prefix


def ranked_candidates(room, name: str, limit_per_class: int) -> list[tuple[str, int, int]]:
    measured = room.scan.scanned & room.scan.seen
    choices = []
    for node_id in candidate_nodes(room.graph):
        index = next(index for index, node in enumerate(room.graph.nodes) if node.id == node_id)
        points = int(((room.owners == index) & measured).sum())
        if points >= 100:
            choices.append((name, index, points))
    if limit_per_class <= 0:
        return sorted(choices, key=lambda item: item[2], reverse=True)
    selected = []
    for label in ("sofa", "chair", "table", "bed", "stool"):
        of_class = [item for item in choices if furniture_class(room.graph.nodes[item[1]]) == label]
        selected.extend(sorted(of_class, key=lambda item: item[2], reverse=True)[:limit_per_class])
    return selected


def run_candidate(room, node_id, directory: pathlib.Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory / "metrics.json"
    if report_path.is_file():
        cached = json.loads(report_path.read_text())
        if cached.get("status") not in {"failed", "blocked"}:
            return cached
    try:
        report = run_evidence(room, node_id, directory)
    except Exception as error:
        report = {"node_id": str(node_id), "status": "failed", "error": str(error)}
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def floor_mesh(source: pathlib.Path, destination: pathlib.Path, transform: list, correction: float) -> pathlib.Path:
    mesh = trimesh.load(source, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"SPAR3D output is not a mesh: {source}")
    matrix = np.asarray(transform, dtype=np.float64).reshape(4, 4).copy()
    matrix[2, 3] += correction
    mesh.apply_transform(matrix)
    mesh.export(destination)
    return destination


def current_build(database: Database):
    with database.connect() as connection:
        graph_row = connection.execute(
            "SELECT graph_json FROM revisions WHERE scan_id=? ORDER BY revision DESC LIMIT 1", (str(SCAN_ID),)
        ).fetchone()
        build_row = connection.execute(
            "SELECT * FROM texture_builds WHERE scan_id=? AND result_json IS NOT NULL ORDER BY id DESC LIMIT 1",
            (str(SCAN_ID),),
        ).fetchone()
    if graph_row is None or build_row is None:
        raise RuntimeError("A-102 graph and patched texture build must exist before SPAR3D")
    if json.loads(build_row["inputs_json"]).get("pipeline") != "patched-library-v1":
        raise RuntimeError("A-102 has not published the LiDAR-patched room build")
    return SceneGraph.model_validate_json(graph_row["graph_json"]), build_row


def publish_results(database: Database, graph: SceneGraph, build, directory: pathlib.Path, reports: dict) -> None:
    floor_indices = {node.id: index for index, node in enumerate(graph.nodes)}
    accepted = []
    for report in reports.values():
        if not report.get("accepted_for_display"):
            continue
        node_id = uuid.UUID(report["node_id"])
        candidate = directory / "furniture-work" / report["source_room"] / str(node_id) / "floor-fitted.glb"
        if node_id not in floor_indices or not candidate.is_file():
            raise RuntimeError(f"accepted A-102 furniture has no floor mesh: {node_id}")
        accepted.append((floor_indices[node_id], candidate))
    output = directory / "scan-furniture.glb"
    if accepted:
        _accepted_mesh(directory / "scan.glb", graph, accepted, output)
    summary = {"scan_id": str(SCAN_ID), "build_id": build["build_key"], "objects": list(reports.values()), "accepted": len(accepted)}
    (directory / "furniture.json").write_text(json.dumps(summary, indent=2) + "\n")
    texture = TextureBuild.model_validate_json(build["result_json"])
    scan_url = (
        furniture_mesh_url(SCAN_ID, build["build_key"], output) if accepted
        else build_prefix(SCAN_ID, build["build_key"]) + "/scan.glb"
    )
    changed = texture.model_copy(update={"scan_glb_url": scan_url})
    with database.transaction() as connection:
        connection.execute("UPDATE texture_builds SET result_json=? WHERE id=?", (changed.model_dump_json(), build["id"]))
    print(json.dumps({"accepted": len(accepted), "evaluated": len(reports)}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room", choices=tuple(ROOMS))
    parser.add_argument("--limit-per-class", type=int, default=0, help="per room; 0 tries every evidenced object")
    args = parser.parse_args()
    missing = [name for name in ROOMS if not (ENHANCEMENTS_DIR / f"{name}-graph.json").is_file()]
    if missing:
        raise RuntimeError(f"A-102 recognition has not finished these rooms: {', '.join(missing)}")
    database = Database(DATA_DIR / "standardphysics.sqlite3")
    store = ArtifactStore(DATA_DIR, max_bytes=0)
    graph, build = current_build(database)
    transforms = json.loads(TRANSFORMS_PATH.read_text())["room_transforms"]
    directory = build_dir(store, SCAN_ID) / build["build_key"]
    floor_ids = {node.id for node in graph.nodes}
    report_path = directory / "furniture.json"
    previous = json.loads(report_path.read_text())["objects"] if report_path.is_file() else []
    reports = {(report["source_room"], report["node_id"]): report for report in previous}
    selected = set()
    for name in ROOMS:
        if args.room is not None and name != args.room:
            continue
        room = source_room_evidence(name)
        for _, index, points in ranked_candidates(room, name, args.limit_per_class):
            node = room.graph.nodes[index]
            selected.add((name, str(node.id)))
            if node.id not in floor_ids:
                reports[(name, str(node.id))] = {
                    "node_id": str(node.id), "source_room": name,
                    "status": "skipped", "reason": "object is not on A-102",
                }
                continue
            candidate_dir = directory / "furniture-work" / name / str(node.id)
            report = run_candidate(room, node.id, candidate_dir)
            report["source_room"] = name
            report["photographed_points"] = points
            reports[(name, str(node.id))] = report
            print(json.dumps({"room": name, "node_id": str(node.id), "status": report["status"]}), flush=True)
            if not report.get("accepted_for_display"):
                continue
            correction = DEFAULT_FLOOR_ORIGIN_CORRECTIONS_M["bottom" if name == "bottom_left" else name]
            floor_mesh(
                candidate_dir / "fitted.glb", candidate_dir / "floor-fitted.glb", transforms[name], correction,
            )
    reports = {
        key: report for key, report in reports.items()
        if key in selected or (args.room is not None and key[0] != args.room)
    }
    publish_results(database, graph, build, directory, reports)


if __name__ == "__main__":
    main()
