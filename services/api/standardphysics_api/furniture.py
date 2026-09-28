"""Quality-gated SPAR3D jobs for photographed, measured furniture."""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import uuid

import numpy as np
import trimesh
from standardphysics_contracts import SceneGraph, TextureBuild
from standardphysics_pipeline.textures.scan_colour import vertex_normals
from standardphysics_pipeline.textures.surface_materials import room_owners

from . import repository as repo
from .textures import build_dir, build_prefix

FURNITURE = "furniture"
FURNITURE_CLASSES = frozenset({"chair", "sofa", "table", "bed", "stool"})
INFERENCE_SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "spar3d_furniture_experiment.py"


def furniture_mesh_url(scan_id: uuid.UUID, build_key: str, path: pathlib.Path) -> str:
    version = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return build_prefix(scan_id, build_key) + f"/scan-furniture.glb?v={version}"


def furniture_class(node) -> str | None:
    label = node.label.strip().casefold()
    if node.labeled_by == "discovery":
        return label if label in FURNITURE_CLASSES else None
    return node.raw_category if node.raw_category in FURNITURE_CLASSES else None


def candidate_nodes(graph: SceneGraph) -> list[uuid.UUID]:
    return [
        node.id for node in graph.nodes
        if node.kind == "object" and furniture_class(node) is not None
        and min(node.dimensions.x, node.dimensions.y, node.dimensions.z) > 0
    ]


def queue_furniture(database, worker, scan_id: uuid.UUID, build_id: int) -> None:
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT result_json, inputs_json FROM texture_builds WHERE id=? AND scan_id=?",
            (build_id, str(scan_id)),
        ).fetchone()
        if row is None or row["result_json"] is None:
            return
        inputs = json.loads(row["inputs_json"])
        build = TextureBuild.model_validate_json(row["result_json"])
        if not inputs.get("lidar") or not inputs.get("frames") or not build.scan_glb_url:
            return
        repo.enqueue_job(connection, scan_id, FURNITURE, build_id)
    worker.wake()


def _run_candidates(scan_id: uuid.UUID, node_ids: list[uuid.UUID], directory: pathlib.Path) -> list[dict]:
    if not node_ids:
        return []
    directory.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, str(INFERENCE_SCRIPT), "--scan-id", str(scan_id),
        "--output-dir", str(directory),
    ]
    for node_id in node_ids:
        command.extend(("--batch-node", str(node_id)))
    result = subprocess.run(command, capture_output=True, text=True)
    (directory / "process.log").write_text((result.stdout + "\n" + result.stderr)[-6000:])
    reports = []
    for node_id in node_ids:
        result_path = directory / str(node_id) / "metrics.json"
        reports.append(
            json.loads(result_path.read_text()) if result_path.is_file()
            else {"node_id": str(node_id), "status": "failed", "error": f"SPAR3D process exited {result.returncode}"}
        )
    return reports


def _accepted_mesh(
    base_path: pathlib.Path, graph: SceneGraph,
    accepted: list[tuple[int, pathlib.Path]], output: pathlib.Path,
) -> None:
    base = trimesh.load(base_path, force="mesh")
    if not isinstance(base, trimesh.Trimesh):
        raise ValueError("the painted scan is not a triangle mesh")
    owners = room_owners(base.vertices, vertex_normals(base.vertices, base.faces), graph)
    remove = np.zeros(len(base.faces), dtype=bool)
    meshes = [base]
    for index, path in accepted:
        remove |= np.all(owners[base.faces] == index, axis=1)
        fitted = trimesh.load(path, force="mesh")
        if not isinstance(fitted, trimesh.Trimesh):
            raise ValueError(f"SPAR3D output is not a triangle mesh: {path}")
        meshes.append(fitted)
    base.update_faces(~remove)
    base.remove_unreferenced_vertices()
    combined = trimesh.util.concatenate(meshes)
    temporary = output.with_name(f".{output.name}.tmp")
    combined.export(temporary, file_type="glb")
    temporary.replace(output)


def run_furniture(database, store, scan_id: uuid.UUID, build_id: int) -> None:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM texture_builds WHERE id=? AND scan_id=?", (build_id, str(scan_id))
        ).fetchone()
    if row is None or not row["result_json"]:
        raise ValueError("furniture job has no completed texture build")
    graph = SceneGraph.model_validate_json(row["graph_json"])
    directory = build_dir(store, scan_id) / row["build_key"]
    reports = _run_candidates(scan_id, candidate_nodes(graph), directory / "furniture-work")
    accepted = [
        (index, directory / "furniture-work" / str(node.id) / "fitted.glb")
        for index, node in enumerate(graph.nodes)
        if any(report.get("node_id") == str(node.id) and report.get("accepted_for_display") for report in reports)
    ]
    output = directory / "scan-furniture.glb"
    if accepted:
        _accepted_mesh(directory / "scan.glb", graph, accepted, output)
    report = {"scan_id": str(scan_id), "build_id": row["build_key"], "objects": reports, "accepted": len(accepted)}
    (directory / "furniture.json").write_text(json.dumps(report, indent=2) + "\n")
    if accepted:
        texture = TextureBuild.model_validate_json(row["result_json"])
        changed = texture.model_copy(update={
            "scan_glb_url": furniture_mesh_url(scan_id, row["build_key"], output)
        })
        with database.transaction() as connection:
            connection.execute(
                "UPDATE texture_builds SET result_json=? WHERE id=?", (changed.model_dump_json(), build_id),
            )
    failed = sum(report.get("status") in {"failed", "blocked"} for report in reports)
    if failed:
        raise RuntimeError(f"{failed} furniture candidates need retry")
