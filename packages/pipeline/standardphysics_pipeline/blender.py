"""Driving Blender from the pipeline.

Blender runs as a subprocess because `bpy` as a library pins a Python version
we do not control. Every script lives in `blender_scripts/` and takes its
arguments after a bare `--`.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile
from typing import NamedTuple

from standardphysics_contracts import SceneGraph

from .check_blender import blender_path

SCRIPTS = pathlib.Path(__file__).parent / "blender_scripts"

TIMEOUT_SECONDS = 300
MIN_DISPLAY_WALL_THICKNESS = 0.08


class BlenderError(RuntimeError):
    pass


def _run(script: str, args: list[str]) -> str:
    result = subprocess.run(
        [blender_path(), "--background", "--python", str(SCRIPTS / script), "--", *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise BlenderError(result.stderr[-2000:] or result.stdout[-2000:])
    return result.stdout


def export_glb(graph: SceneGraph, out_path: pathlib.Path) -> pathlib.Path:
    """Display geometry for the viewer, named by node ID.

    glTF node names take a UUID directly, so the viewer selects by the same ID
    the checks reason about with no mapping file in between.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        handle.write(display_graph(graph).model_dump_json())
        graph_path = handle.name

    output = _run("build_glb.py", ["--graph", graph_path, "--out", str(out_path)])
    pathlib.Path(graph_path).unlink(missing_ok=True)

    if "GLB_WRITTEN" not in output:
        raise BlenderError(f"export did not report success:\n{output[-2000:]}")
    return out_path


class ConversionResult(NamedTuple):
    glb_path: pathlib.Path
    imported: int
    meshes: int
    renamed: int
    unmapped_count: int
    unmapped_sample: list[str]
    """At most ten names, for a log line. `unmapped_count` is the real number."""

    @property
    def fully_identified(self) -> bool:
        """Every mesh ties back to a node a check can reason about.

        Counted over meshes, not over everything imported: a USD scene carries
        grouping nodes that hold no geometry and need no identity.
        """
        return self.meshes > 0 and self.unmapped_count == 0


def usdz_to_glb(
    usdz_path: pathlib.Path,
    out_path: pathlib.Path,
    metadata_path: pathlib.Path | None = None,
) -> ConversionResult:
    """Convert a scanned room into display geometry that keeps its identity.

    Falls back to nothing: if the mapping is missing or partial, the result
    says so rather than shipping a GLB whose meshes cannot be selected. Call
    `export_glb` on the SceneGraph instead when that happens.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    args = ["--usdz", str(usdz_path), "--out", str(out_path)]
    if metadata_path is not None:
        args += ["--map", str(metadata_path)]

    output = _run("usdz_to_glb.py", args)
    summary = next(
        (line for line in output.splitlines() if line.startswith("USDZ_CONVERTED")),
        None,
    )
    if summary is None:
        raise BlenderError(f"conversion did not report success:\n{output[-2000:]}")

    fields = dict(part.split("=") for part in summary.split()[1:])
    unmapped = [
        line.split(" ", 1)[1]
        for line in output.splitlines()
        if line.startswith("UNMAPPED ")
    ]
    return ConversionResult(
        glb_path=out_path,
        imported=int(fields["imported"]),
        meshes=int(fields.get("meshes", fields["imported"])),
        renamed=int(fields["renamed"]),
        unmapped_count=int(fields["unmapped"]),
        unmapped_sample=unmapped,
    )


def glb_node_names(path: pathlib.Path) -> list[str]:
    """Read the node names back out of a GLB, without Blender.

    A GLB is a 12 byte header then length-prefixed chunks; the first chunk is
    the JSON scene description.
    """
    data = path.read_bytes()
    if data[:4] != b"glTF":
        raise BlenderError(f"{path} is not a GLB")
    chunk_length = int.from_bytes(data[12:16], "little")
    scene = json.loads(data[20 : 20 + chunk_length].decode("utf-8"))
    return [node.get("name", "") for node in scene.get("nodes", [])]


def glb_mesh_bounds(path: pathlib.Path, node_id: str) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Evaluate an exported GLB in Blender and return one mesh's world bounds."""
    output = _run("inspect_glb.py", ["--glb", str(path), "--node", node_id])
    result = _blender_json(output)
    if not result["mesh"]:
        raise BlenderError(f"{node_id} did not export as a mesh")
    return tuple(result["min"]), tuple(result["max"])


def glb_ray_hit(path: pathlib.Path, origin: tuple[float, float, float], direction: tuple[float, float, float]) -> str | None:
    """Cast a ray through the exported GLB in Blender and return the hit node ID."""
    values = [str(value) for value in (*origin, *direction)]
    output = _run("inspect_glb.py", ["--glb", str(path), "--node", "unused", "--ray", *values])
    return _blender_json(output)["object"]


def _blender_json(output: str) -> dict:
    for line in reversed(output.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise BlenderError(f"inspection did not return JSON:\n{output[-2000:]}")


def render_finding(
    graph: SceneGraph, locus, out_path: pathlib.Path, size: tuple[int, int] = (1200, 800)
) -> pathlib.Path:
    """One still of a finding, for the printed report.

    The screen version animates: the camera flies in, everything else fades,
    the measurement draws. Paper gets one frame, so the render has to carry the
    same information at once.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    graph_file = _write_temp(display_graph(graph).model_dump_json())
    locus_file = _write_temp(locus.model_dump_json())

    output = _run(
        "render_finding.py",
        [
            "--graph", graph_file, "--locus", locus_file, "--out", str(out_path),
            "--width", str(size[0]), "--height", str(size[1]),
        ],
    )
    pathlib.Path(graph_file).unlink(missing_ok=True)
    pathlib.Path(locus_file).unlink(missing_ok=True)

    if "RENDER_WRITTEN" not in output:
        raise BlenderError(f"render did not report success:\n{output[-2000:]}")
    return out_path


def _write_temp(payload: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        handle.write(payload)
        return handle.name


def display_graph(graph: SceneGraph) -> SceneGraph:
    """Clamp visual-only zero-depth shells; measurements keep their original dimensions."""
    nodes = [
        _display_node(node)
        for node in graph.nodes
    ]
    return graph.model_copy(update={"nodes": nodes})


def _display_node(node):
    """Anything with no thickness gets just enough to be visible.

    A wall is measured as a plane and a floor as a sheet, so both come back with
    an extent of nothing in some direction and neither can be drawn. Which one it
    is does not matter: an extent of zero cannot be seen, so every one of them is
    opened to the smallest width that can. The measurements are untouched, and
    only what is drawn changes.
    """
    flattened = {
        axis: MIN_DISPLAY_WALL_THICKNESS
        for axis in ("x", "y", "z")
        if getattr(node.dimensions, axis) < MIN_DISPLAY_WALL_THICKNESS
    }
    if not flattened:
        return node
    return node.model_copy(update={"dimensions": node.dimensions.model_copy(update=flattened)})
