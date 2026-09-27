"""The whole library floor: several room captures, each painted, then set side by side.

`paint_the_scan` colours one capture in its own room frame. A floor is several captures
that were walked separately, so each one arrives in its own frame with its own idea of
where the origin is. This module paints each room by the same pass, fills the vertices no
camera reached, and applies the caller's 4x4 `to_floor` matrix to move the room to where
it belongs relative to the others.

The rooms are concatenated, not welded. Where two captures cover the same stretch of
floor, that stretch appears in the output twice, held apart by whatever residual the
caller's registration left, and a viewer shows both copies fighting for the same pixels.
Welding them would mean deciding which capture is right about the surface, and nothing
here knows that.

Coverage reports the share of vertices a camera actually measured. Filling gives every
vertex a colour so the finished model has no grey holes, but a filled vertex carries a
neighbour's colour rather than a measurement and is never counted as one.
"""

from __future__ import annotations

import pathlib
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np
from scipy.spatial import KDTree
from standardphysics_contracts import Mat4

from .camera import PhotoCamera, load_cameras
from .project import evenly_spread
from .scan_colour import (
    MAX_PHOTOS,
    ColouredScan,
    _photo,
    colour_the_scan,
    scan_geometry,
    unused_vertices_removed,
    write_scan_glb,
)

MAX_FLOOR_TRIANGLES = 850_000
"""What the merged floor is decimated to before it is written, so the viewer can load it."""
JOINED_FLOOR_TRIANGLES = 1_100_000
"""Enough for four walks already thinned to 260,000 each, so joining them never thins them again.

A second thinning moves the corners of faces whose texels were baked where the
corners were, and the photographs slide across the surface.
"""


@dataclass(frozen=True)
class RoomCapture:
    """One walked room and the matrix that puts it on the shared floor."""

    name: str
    mesh_path: pathlib.Path
    poses_path: pathlib.Path
    frame_paths: dict[str, pathlib.Path]
    capture_to_room: Mat4
    to_floor: np.ndarray
    """4x4, row-major, moving this room's own frame into the shared floor frame."""
    max_photos: int = MAX_PHOTOS


@dataclass(frozen=True)
class LibraryPaint:
    glb_path: pathlib.Path
    directly_observed_fraction: float
    """The share of vertices a camera measured, before any filling."""
    photos_used: int
    vertices: int
    triangles: int
    seconds: float


def unobserved_filled_from_nearest(scan: ColouredScan) -> ColouredScan:
    """Every vertex no camera reached takes the colour of the nearest one that was reached.

    This clears the neutral grey patches under tables and behind pillars. Nearest here is
    nearest in space rather than nearest along the surface, so a vertex under a tabletop
    can inherit the floor beneath it. `seen` is left exactly as it was, because a filled
    vertex is a guess and coverage has to keep reporting what a camera measured.
    """
    observed = np.flatnonzero(scan.seen)
    unobserved = np.flatnonzero(~scan.seen)
    if not len(observed) or not len(unobserved):
        return scan
    _, nearest = KDTree(scan.vertices[observed]).query(scan.vertices[unobserved], k=1)
    colours = scan.colours.copy()
    colours[unobserved] = scan.colours[observed[nearest]]
    return replace(scan, colours=colours)


def moved_to_floor(scan: ColouredScan, to_floor: np.ndarray) -> ColouredScan:
    """The same scan with its vertices carried into the shared floor frame."""
    matrix = np.asarray(to_floor, dtype=np.float64).reshape(4, 4)
    return replace(scan, vertices=scan.vertices @ matrix[:3, :3].T + matrix[:3, 3])


def rooms_concatenated(scans: Sequence[ColouredScan]) -> ColouredScan:
    """One scan holding every room, each room's triangles renumbered into the merged array."""
    if not scans:
        raise ValueError("no rooms to merge")
    triangles, vertices_so_far = [], 0
    for scan in scans:
        triangles.append(scan.triangles + vertices_so_far)
        vertices_so_far += len(scan.vertices)
    return ColouredScan(
        vertices=np.concatenate([scan.vertices for scan in scans]),
        triangles=np.concatenate(triangles),
        colours=np.concatenate([scan.colours for scan in scans]),
        seen=np.concatenate([scan.seen for scan in scans]),
    )


def _cameras_for(room: RoomCapture) -> list[PhotoCamera]:
    cameras = [
        camera for camera in load_cameras(room.poses_path, room.frame_paths, room.capture_to_room)
        if room.frame_paths.get(camera.frame_id, pathlib.Path()).is_file()
    ]
    if not cameras:
        raise ValueError(f"{room.name}: no stored photo has a usable camera pose")
    return evenly_spread(cameras, room.max_photos)


def painted_room(room: RoomCapture) -> tuple[ColouredScan, int]:
    """One room coloured from its own photos, filled in, and placed on the floor."""
    vertices, triangles = scan_geometry(room.mesh_path, room.capture_to_room)
    cameras = _cameras_for(room)
    images = [_photo(room.frame_paths[camera.frame_id]) for camera in cameras]
    resized = [camera.resized(*image.shape[1::-1]) for camera, image in zip(cameras, images)]
    scan = unused_vertices_removed(colour_the_scan(vertices, triangles, resized, images))
    return moved_to_floor(unobserved_filled_from_nearest(scan), room.to_floor), len(cameras)


def painted_scans_joined(
    scans: Sequence[tuple[pathlib.Path, np.ndarray]],
    out_path: pathlib.Path,
    max_triangles: int = JOINED_FLOOR_TRIANGLES,
) -> pathlib.Path:
    """Rooms already painted one by one, each moved by its 4x4 `to_floor` and written as one glTF.

    Painting a floor in one pass spreads one photo budget over every room, so each
    room is painted from a fraction of the photos it gets alone. Rooms that were
    already painted keep their own paint, and only their placement is new.
    """
    from ..blender import _run

    out_path.parent.mkdir(parents=True, exist_ok=True)
    command = ["--out", str(out_path), "--max-triangles", str(max_triangles)]
    for glb_path, to_floor in scans:
        values = np.asarray(to_floor, dtype=np.float64).reshape(16)
        command.extend(["--scan", str(glb_path), "--placement", " ".join(f"{value:.9g}" for value in values)])
    output = _run("join_scans.py", command)
    if "FLOOR_GLB_WRITTEN" not in output:
        raise RuntimeError(f"Blender did not write the floor:\n{output[-1500:]}")
    return out_path


def paint_the_rooms(
    rooms: list[RoomCapture],
    out_path: pathlib.Path,
    max_triangles: int | None = MAX_FLOOR_TRIANGLES,
) -> LibraryPaint:
    """Every room painted and merged into one glTF the viewer can show as a single floor."""
    started = time.monotonic()
    painted = [painted_room(room) for room in rooms]
    floor = rooms_concatenated([scan for scan, _ in painted])
    write_scan_glb(floor, out_path, max_triangles=max_triangles)
    return LibraryPaint(
        glb_path=out_path,
        directly_observed_fraction=floor.painted_fraction,
        photos_used=sum(photos for _, photos in painted),
        vertices=len(floor.vertices),
        triangles=len(floor.triangles),
        seconds=time.monotonic() - started,
    )
