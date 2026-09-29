"""Quality-gated SPAR3D jobs for photographed, measured furniture.

SPAR3D runs in an interpreter and a source checkout of its own, which the
production image does not carry. A server that has them names them in
SP_FURNITURE_PYTHON and SP_FURNITURE_SOURCE; one that doesn't never queues a
furniture job, reports the feature as unavailable, and shows the painted scan as it is.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import uuid
from dataclasses import dataclass

import numpy as np
import trimesh
from standardphysics_contracts import SceneGraph, TextureBuild
from standardphysics_pipeline.textures.scan_colour import vertex_normals
from standardphysics_pipeline.textures.surface_materials import room_owners

from . import repository_jobs as jobs_repo
from .settings import Settings
from .textures import build_dir, build_prefix

FURNITURE = "furniture"
FURNITURE_CLASSES = frozenset({"chair", "sofa", "table", "bed", "stool"})
INFERENCE_SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "spar3d_furniture_experiment.py"
UNAVAILABLE = "Furniture refinement isn't available on this server."
LONGEST_PROCESS_LOG = 6000


class FurnitureUnavailable(RuntimeError):
    """A furniture job ran on a server with no SPAR3D runtime configured."""


@dataclass(frozen=True)
class FurnitureRuntime:
    """Where SPAR3D lives on this server, and how long one refinement may take."""

    python: pathlib.Path
    source: pathlib.Path
    timeout_seconds: float

    def environment(self) -> dict[str, str]:
        """The script's environment, naming the runtime it hands inference to."""
        return {**os.environ, "SP_FURNITURE_PYTHON": str(self.python), "SP_FURNITURE_SOURCE": str(self.source)}


def runtime_problem(settings: Settings) -> str | None:
    """Why furniture refinement can't run here, or None when it can. It names settings, never paths."""
    if settings.furniture_python is None or settings.furniture_source is None:
        return "SP_FURNITURE_PYTHON and SP_FURNITURE_SOURCE are not set"
    if not settings.furniture_python.is_file():
        return "SP_FURNITURE_PYTHON is not a file"
    if not settings.furniture_source.is_dir():
        return "SP_FURNITURE_SOURCE is not a directory"
    if not INFERENCE_SCRIPT.is_file():
        return "the furniture script is missing from this install"
    return None


def furniture_runtime(settings: Settings) -> FurnitureRuntime | None:
    python, source = settings.furniture_python, settings.furniture_source
    if python is None or source is None or runtime_problem(settings) is not None:
        return None
    return FurnitureRuntime(python, source, settings.furniture_timeout_seconds)


def furniture_health(settings: Settings) -> dict:
    problem = runtime_problem(settings)
    return {"available": problem is None, "reason": problem}


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
    if furniture_runtime(worker.settings) is None:
        return
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
        jobs_repo.enqueue_job(connection, scan_id, FURNITURE, build_id)
    worker.wake()


def _script_output(output: str | bytes | None) -> str:
    return output.decode(errors="replace") if isinstance(output, bytes) else output or ""


def _launch(command: list[str], runtime: FurnitureRuntime, log_path: pathlib.Path) -> str | None:
    """Run the script to its end or its timeout, keep the tail of what it printed,
    and say why it failed, or None when it exited cleanly."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, env=runtime.environment(), timeout=runtime.timeout_seconds
        )
    except subprocess.TimeoutExpired as expired:
        output = _script_output(expired.stdout) + "\n" + _script_output(expired.stderr)
        log_path.write_text(output[-LONGEST_PROCESS_LOG:])
        return f"SPAR3D process was stopped after {runtime.timeout_seconds:g} seconds"
    log_path.write_text((result.stdout + "\n" + result.stderr)[-LONGEST_PROCESS_LOG:])
    return f"SPAR3D process exited {result.returncode}" if result.returncode else None


def _run_candidates(
    scan_id: uuid.UUID, node_ids: list[uuid.UUID], directory: pathlib.Path, runtime: FurnitureRuntime
) -> list[dict]:
    if not node_ids:
        return []
    directory.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, str(INFERENCE_SCRIPT), "--scan-id", str(scan_id),
        "--output-dir", str(directory),
    ]
    for node_id in node_ids:
        command.extend(("--batch-node", str(node_id)))
    failure = _launch(command, runtime, directory / "process.log") or "SPAR3D process wrote no result"
    reports = []
    for node_id in node_ids:
        result_path = directory / str(node_id) / "metrics.json"
        reports.append(
            json.loads(result_path.read_text()) if result_path.is_file()
            else {"node_id": str(node_id), "status": "failed", "error": failure}
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


def run_furniture(database, store, scan_id: uuid.UUID, build_id: int, runtime: FurnitureRuntime | None) -> None:
    if runtime is None:
        raise FurnitureUnavailable(UNAVAILABLE)
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM texture_builds WHERE id=? AND scan_id=?", (build_id, str(scan_id))
        ).fetchone()
    if row is None or not row["result_json"]:
        raise ValueError("furniture job has no completed texture build")
    graph = SceneGraph.model_validate_json(row["graph_json"])
    directory = build_dir(store, scan_id) / row["build_key"]
    reports = _run_candidates(scan_id, candidate_nodes(graph), directory / "furniture-work", runtime)
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
