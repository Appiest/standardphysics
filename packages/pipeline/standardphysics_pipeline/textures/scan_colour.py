"""Painting the scanned surface itself, rather than the boxes that stand in for it.

The measured model is boxes, and boxes are the right shape for measuring a gap,
colliding with a wheelchair and being dragged across a floor. They are the
wrong shape to photograph. On a real capture the generated box tops miss the
surfaces under them by inches — a chair by twenty-one, a bench by twenty, a
sofa by eight — and a photo projected onto a plane floating above the thing it
depicts slides sideways with the viewing angle. That is why a laptop keyboard
ends up painted flat across a desk: the pixels are real, the surface they land
on is not where the surface is.

The LiDAR mesh has no such gap, because it *is* the surface. The tabletop sits
at the tabletop's height and the laptop standing on it is geometry, so a photo
lands where it belongs and the laptop occludes the desk by simply being in the
way.

Colour goes on the vertices rather than into an atlas. The mesh already has a
vertex every centimetre or so, which is finer than the photos resolve at room
distance, and it means no unwrapping, no charts, no seams and no gutters.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

import numpy as np

from ..lidar import load_mesh
from .camera import PhotoCamera, load_cameras
from .project import bilinear, depth_buffer, to_linear, to_srgb

MAX_PHOTOS = 60
"""Photos read for colour. Every vertex keeps only its best view, so more
photos raise coverage and never blend; this is where the gain flattens."""
MAX_PHOTO_EDGE = 1600

SEEN_TOLERANCE = 0.05
"""How close to the nearest scanned surface a vertex must be to count as seen."""
MIN_FACING = 0.20
BORDER_FALLOFF_PIXELS = 24.0
UNSEEN = np.array([0.62, 0.60, 0.58], dtype=np.float32)
"""What a vertex no photo reached is left as: the scan's own neutral grey."""


@dataclass(frozen=True)
class ColouredScan:
    vertices: np.ndarray
    triangles: np.ndarray
    colours: np.ndarray
    """One sRGB colour per vertex, 0-1."""
    seen: np.ndarray
    """Whether any photo reached each vertex."""

    @property
    def painted_fraction(self) -> float:
        return float(self.seen.mean()) if len(self.seen) else 0.0


def vertex_normals(vertices: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """Area-weighted normals, so a vertex faces the way its surface faces."""
    corners = vertices[triangles]
    face = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normals = np.zeros_like(vertices)
    for column in range(3):
        np.add.at(normals, triangles[:, column], face)
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    return normals / np.maximum(length, 1e-9)


def _weights_from(
    camera: PhotoCamera, vertices: np.ndarray, normals: np.ndarray, buffer: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """How good this camera's view of each vertex is, and where to sample it."""
    columns, rows, depth = camera.project(vertices)
    toward = camera.position[None, :] - vertices
    distance = np.linalg.norm(toward, axis=1)
    facing = np.einsum("ij,ij->i", normals, toward) / np.maximum(distance, 1e-9)
    inside = (
        (depth > 0.2) & (facing > MIN_FACING)
        & (columns >= 0) & (columns <= camera.width - 1)
        & (rows >= 0) & (rows <= camera.height - 1)
    )
    height, width = buffer.shape
    nearest = buffer[
        np.clip(np.rint(rows * height / camera.height).astype(np.int64), 0, height - 1),
        np.clip(np.rint(columns * width / camera.width).astype(np.int64), 0, width - 1),
    ]
    unhidden = ~np.isfinite(nearest) | (depth <= nearest + SEEN_TOLERANCE)
    border = np.clip(
        np.minimum.reduce([columns, rows, camera.width - 1 - columns, camera.height - 1 - rows])
        / BORDER_FALLOFF_PIXELS, 0.0, 1.0,
    )
    weight = np.where(inside & unhidden, facing ** 2 / np.maximum(distance, 0.5) * border, 0.0)
    return weight, columns, rows


def colour_the_scan(
    vertices: np.ndarray,
    triangles: np.ndarray,
    cameras: list[PhotoCamera],
    images: list[np.ndarray],
) -> ColouredScan:
    """Every vertex given the colour of the photo that saw it best."""
    normals = vertex_normals(vertices, triangles)
    best = np.zeros(len(vertices), dtype=np.float32)
    colours = np.tile(UNSEEN, (len(vertices), 1))
    for camera, photo in zip(cameras, images):
        buffer = depth_buffer(camera, vertices)
        weight, columns, rows = _weights_from(camera, vertices, normals, buffer)
        better = np.flatnonzero(weight > best)
        if not len(better):
            continue
        sampled = bilinear(photo, columns[better], rows[better])
        colours[better] = to_srgb(to_linear(sampled.astype(np.float32)))
        best[better] = weight[better]
    return ColouredScan(vertices, triangles, np.clip(colours, 0.0, 1.0), best > 0)


def scan_geometry(mesh_path: pathlib.Path, capture_to_room) -> tuple[np.ndarray, np.ndarray]:
    """Room-frame vertices and the triangles that index them, welded across anchors."""
    mesh = load_mesh(mesh_path)
    if mesh is None:
        raise ValueError(f"not a LiDAR mesh: {mesh_path}")
    matrix = np.asarray(capture_to_room.m, dtype=np.float64).reshape(4, 4)
    vertices, triangles, offset = [], [], 0
    for part in mesh.parts:
        local = np.asarray(part.transform, dtype=np.float64).reshape(4, 4, order="F")
        own = np.asarray(part.vertices, dtype=np.float64).reshape(-1, 3)
        own = own @ local[:3, :3].T + local[:3, 3]
        vertices.append(own @ matrix[:3, :3].T + matrix[:3, 3])
        triangles.append(np.asarray(part.triangles, dtype=np.int64).reshape(-1, 3) + offset)
        offset += len(own)
    return np.concatenate(vertices), np.concatenate(triangles)


def unused_vertices_removed(scan: ColouredScan) -> ColouredScan:
    """Drop anything no triangle refers to, which the anchors leave behind."""
    used = np.unique(scan.triangles)
    remap = np.full(len(scan.vertices), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return ColouredScan(
        vertices=scan.vertices[used],
        triangles=remap[scan.triangles],
        colours=scan.colours[used],
        seen=scan.seen[used],
    )


def write_scan_glb(scan: ColouredScan, out_path: pathlib.Path, max_triangles: int | None = None) -> pathlib.Path:
    """Hand the coloured scan to Blender, which writes the glTF the viewer reads."""
    import tempfile

    from ..blender import _run

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="standardphysics-scan-") as temporary:
        archive = pathlib.Path(temporary) / "scan.npz"
        np.savez(
            archive,
            vertices=scan.vertices.astype(np.float32),
            triangles=scan.triangles.astype(np.int32),
            colours=scan.colours.astype(np.float32),
        )
        command = ["--scan", str(archive), "--out", str(out_path)]
        if max_triangles is not None:
            command.extend(["--max-triangles", str(max_triangles)])
        output = _run("colour_scan.py", command)
    if "SCAN_GLB_WRITTEN" not in output:
        raise RuntimeError(f"Blender did not write the scan:\n{output[-1500:]}")
    return out_path


@dataclass(frozen=True)
class ScanPaint:
    glb_path: pathlib.Path
    painted_fraction: float
    photos_used: int
    seconds: float


def _evenly_spread(cameras: list[PhotoCamera], limit: int) -> list[PhotoCamera]:
    if len(cameras) <= limit:
        return cameras
    picks = np.linspace(0, len(cameras) - 1, limit).round().astype(int)
    return [cameras[index] for index in dict.fromkeys(picks.tolist())]


def _photo(path: pathlib.Path) -> np.ndarray:
    from PIL import Image

    with Image.open(path) as opened:
        image = opened.convert("RGB")
        image.thumbnail((MAX_PHOTO_EDGE, MAX_PHOTO_EDGE), Image.Resampling.LANCZOS)
        return np.asarray(image, dtype=np.float32) / 255.0


def paint_the_scan(
    mesh_path: pathlib.Path,
    poses_path: pathlib.Path,
    frame_paths: dict[str, pathlib.Path],
    capture_to_room,
    out_path: pathlib.Path,
) -> ScanPaint:
    """The captured surface, coloured from the photos, as a glTF the viewer can show."""
    import time

    started = time.monotonic()
    vertices, triangles = scan_geometry(mesh_path, capture_to_room)
    cameras = [
        camera for camera in load_cameras(poses_path, frame_paths, capture_to_room)
        if frame_paths.get(camera.frame_id, pathlib.Path()).is_file()
    ]
    cameras = _evenly_spread(cameras, MAX_PHOTOS)
    if not cameras:
        raise ValueError("no stored photo has a usable camera pose")
    resized = [camera.resized(*_photo(frame_paths[camera.frame_id]).shape[1::-1]) for camera in cameras]
    images = [_photo(frame_paths[camera.frame_id]) for camera in cameras]
    scan = unused_vertices_removed(colour_the_scan(vertices, triangles, resized, images))
    write_scan_glb(scan, out_path)
    return ScanPaint(out_path, scan.painted_fraction, len(cameras), time.monotonic() - started)
