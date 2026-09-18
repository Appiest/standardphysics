"""Opening a scan directory: the measured room, and the frames that saw it.

Two layouts hold a scan on this machine. The API writes `artifacts/room-json` and
`artifacts/frame-0000` beside a `poses` file; a phone export writes `room.json`
and a `frames` folder. Both are read here so nothing above has to know which one
it was handed.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

import numpy as np
from standardphysics_contracts import SceneGraph

from ..ingest import parse_room_json
from ..lidar import LidarMeshError, room_cloud
from ..textures.camera import PhotoCamera, load_cameras


class ScanNotReadable(RuntimeError):
    """Raised rather than counting against half a scan."""


@dataclass(frozen=True)
class Scan:
    graph: SceneGraph
    cameras: list[PhotoCamera]
    frames: pathlib.Path
    cloud: np.ndarray | None = None
    """The measured points, which say what the camera actually hit."""

    @property
    def frame_count(self) -> int:
        return len(self.cameras)


def open_scan(directory: pathlib.Path) -> Scan:
    room = _first(directory, ("artifacts/room-json", "room.json", "room-json"))
    poses = _first(directory, ("artifacts/poses", "poses", "poses.json"))
    if room is None or poses is None:
        raise ScanNotReadable(f"{directory} has no room model and poses in it.")
    frames_at, names = _frames(directory)
    if not names:
        raise ScanNotReadable(
            f"{directory} carries no video. Counting from photographs needs the frames."
        )
    graph = parse_room_json(json.loads(room.read_text()))
    return Scan(
        graph=graph,
        cameras=load_cameras(poses, names, graph.capture_to_room),
        frames=frames_at,
        cloud=_cloud(directory, graph),
    )


def _cloud(directory: pathlib.Path, graph: SceneGraph) -> np.ndarray | None:
    """The LiDAR points, when the scan carries them."""
    mesh = _first(directory, ("artifacts/lidar-mesh", "lidar-mesh", "lidar_mesh.json"))
    if mesh is None or graph.capture_to_room is None:
        return None
    try:
        return room_cloud(mesh, graph.capture_to_room)
    except (OSError, ValueError, LidarMeshError):
        return None


def _first(directory: pathlib.Path, names: tuple[str, ...]) -> pathlib.Path | None:
    for name in names:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _frames(directory: pathlib.Path) -> tuple[pathlib.Path, list[str]]:
    for folder, pattern in ((directory / "artifacts", "frame-*"), (directory / "frames", "*.jpg")):
        if folder.is_dir():
            found = sorted(path.name for path in folder.glob(pattern) if path.is_file())
            if found:
                return folder, found
    return directory, []
