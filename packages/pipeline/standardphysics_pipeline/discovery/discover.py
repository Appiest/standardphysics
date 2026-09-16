"""Finding the objects the scan measured but never named.

RoomPlan boxes the categories Apple ships, which are the furniture of a home.
A shop is full of things outside that list: the card reader on the counter, the
monitor behind it, the laptop on the desk, the kettle, the sign. Those arrive
as LiDAR and nothing else, so a check cannot reason about them and the owner
cannot move them.

This walks the capture once and gives them boxes.

    frames        photos, evenly spread across the walk
    detections    one vision pass per frame: what is here, and where in the picture
    people        every person's surface taken out of the mesh first
    carving       each rectangle plus the mesh becomes one measured box
    merging       the same object seen from several places becomes one object
    nodes         a SceneNode per object, sized by LiDAR and named by the photo

The photo only ever supplies a name and a rectangle. Every number comes from
the mesh, so a confident wrong label produces a mislabelled box rather than a
wrong measurement.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import pathlib
import uuid
from dataclasses import dataclass, field

import numpy as np
from standardphysics_contracts import SceneGraph, SceneNode

from ..lidar import LidarMeshError, room_cloud
from ..textures.camera import CameraMetadataError, PhotoCamera, load_cameras
from ..textures.project import depth_buffer
from .boxes import claimed_by_any, contained_fraction, resting_parent
from .cache import DetectionCache
from .carve import FrameView, carve
from .detect import DEFAULT_MODEL, MODEL_ENV, Detection, DetectionError, Transport, detect_objects
from .merge import Candidate, DiscoveredObject, merge_candidates
from .people import without_people

log = logging.getLogger(__name__)

FRAME_LIMIT = 400
"""Every keyframe of a normal walk. A frame nobody reads is a person left in
the mesh and an object that was never there: on a real 110-second capture,
sampling 24 of 218 frames found half the laptops and a quarter of the people."""
DETECTION_WORKERS = 5
"""Enough to keep the walk short, few enough that a long capture does not trip
the model host's rate limit and lose frames to it."""
MIN_VOLUME = 0.0004
"""Forty cubic centimetres, about a card reader lying flat. Smaller is noise."""
MAX_FLOOR_CLEARANCE = 2.4
CONFIDENT_VIEWS = 3
"""Separate places the object was seen from before its box is worth trusting."""
MIN_VIEWS = 2
"""Fewer separate places than this and it is usually a fragment of something else."""
APART = 0.75
"""How far the phone must move before a second photo counts as a second look.

Keyframes land twice a second, so a dozen of them in a row are one viewpoint
seen twelve times, not twelve viewpoints. Counting frames made a fragment
glimpsed once from a doorway look as well evidenced as a sofa walked around,
and a fragment standing in an aisle turns a real route finding into a request
to go and rescan it."""
ALREADY_MEASURED = 0.6
"""A carved object mostly inside a node RoomPlan already boxed is that node, not a new one."""
ALREADY_THE_ROOM = frozenset({
    "wall", "walls", "floor", "flooring", "ceiling", "door", "doors", "doorway",
    "window", "windows", "blinds", "curtain", "curtains", "window blinds",
    "baseboard", "skirting board", "staircase", "stairs",
})
"""Named things that are the room rather than something in it.

RoomPlan measures the shell, and a second box over the same wall is worse than
no box: it shadows the real one, takes photo colour meant for it, and offers
the owner a wall to drag across the floor. The detector names these because
they are there, which is correct of it and not useful to us."""

DISCOVERY_NAMESPACE = uuid.UUID("6f1f6a2e-9a5f-5f77-9a0c-8b6f1b0d4a10")


class DiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class DiscoveryInputs:
    graph: SceneGraph
    poses_path: pathlib.Path
    frame_paths: dict[str, pathlib.Path]
    """Stored JPEG for each frame id, as the photo manifest lists them."""
    lidar_mesh_path: pathlib.Path
    cache_dir: pathlib.Path | None = None
    """Where answers about these photos are kept, so a rebuild asks nothing again."""


@dataclass
class DiscoveryResult:
    nodes: list[SceneNode] = field(default_factory=list)
    objects: list[DiscoveredObject] = field(default_factory=list)
    frames_read: int = 0
    people_points_removed: int = 0
    frames_with_people: int = 0
    failures: list[str] = field(default_factory=list)
    """Frames the vision model could not read. Never silent: a dropped frame is a smaller answer."""

    @property
    def mesh_points(self) -> int:
        return sum(len(object_.box.points) for object_ in self.objects)


def discover_objects(inputs: DiscoveryInputs, *, transport: Transport | None = None) -> DiscoveryResult:
    """Every object in the photos that the measured model does not already hold."""
    graph = inputs.graph
    if graph.capture_to_room is None:
        raise DiscoveryError("the scan has no capture_to_room transform, so photos cannot be projected")
    points = _mesh_points(inputs)
    cameras = _cameras(inputs, graph)
    detections, failures = _detect_all(
        cameras, inputs.frame_paths, transport, _cache_for(inputs), _orientations(inputs.poses_path)
    )
    buffers = {camera.frame_id: depth_buffer(camera, points) for camera in cameras}
    removal = without_people(
        points, graph,
        [(camera, detections.get(camera.frame_id, []), buffers[camera.frame_id]) for camera in cameras],
    )
    unclaimed = removal.points[~claimed_by_any(removal.points, graph)]
    candidates = _carve_all(cameras, detections, unclaimed, buffers)
    found = [
        (object_, _viewpoints(object_, cameras))
        for object_ in merge_candidates(candidates)
    ]
    kept = [pair for pair in found if _worth_keeping(pair[0], graph, pair[1])]
    objects = [object_ for object_, _ in kept]
    return DiscoveryResult(
        nodes=[_node_for(object_, graph, viewpoints) for object_, viewpoints in kept],
        objects=objects,
        frames_read=len(cameras) - len(failures),
        people_points_removed=removal.removed,
        frames_with_people=removal.frames_with_people,
        failures=failures,
    )


def _mesh_points(inputs: DiscoveryInputs) -> np.ndarray:
    try:
        points = room_cloud(inputs.lidar_mesh_path, inputs.graph.capture_to_room)
    except (LidarMeshError, OSError) as error:
        raise DiscoveryError(f"could not read the LiDAR mesh: {error}") from error
    if not len(points):
        raise DiscoveryError("the LiDAR mesh is empty")
    return points


def _cameras(inputs: DiscoveryInputs, graph: SceneGraph) -> list[PhotoCamera]:
    try:
        cameras = load_cameras(inputs.poses_path, inputs.frame_paths, graph.capture_to_room)
    except CameraMetadataError as error:
        raise DiscoveryError(f"the photos carry no usable camera metadata: {error}") from error
    stored = [camera for camera in cameras if inputs.frame_paths.get(camera.frame_id, pathlib.Path()).is_file()]
    if not stored:
        raise DiscoveryError("no stored photo has a matching camera pose")
    return _evenly_spread(stored, FRAME_LIMIT)


def _evenly_spread(cameras: list[PhotoCamera], limit: int) -> list[PhotoCamera]:
    if len(cameras) <= limit:
        return cameras
    picks = np.linspace(0, len(cameras) - 1, limit).round().astype(int)
    return [cameras[index] for index in dict.fromkeys(picks.tolist())]


def _orientations(poses_path: pathlib.Path) -> dict[str, str]:
    """Which way up the phone was for each frame, so the model is shown it upright."""
    try:
        payload = json.loads(pathlib.Path(poses_path).read_bytes())
    except (OSError, ValueError):
        return {}
    return {
        item["frame_id"]: item.get("orientation", "")
        for item in payload
        if isinstance(item, dict) and item.get("frame_id")
    }


def _cache_for(inputs: DiscoveryInputs) -> DetectionCache | None:
    if inputs.cache_dir is None:
        return None
    return DetectionCache(inputs.cache_dir, os.environ.get(MODEL_ENV) or DEFAULT_MODEL)


def _detect_all(
    cameras: list[PhotoCamera],
    frame_paths: dict[str, pathlib.Path],
    transport: Transport | None,
    cache: DetectionCache | None,
    orientations: dict[str, str],
) -> tuple[dict[str, list[Detection]], list[str]]:
    detections: dict[str, list[Detection]] = {}
    failures: list[str] = []
    wanted = _not_already_read(cameras, frame_paths, cache, detections, orientations)
    with concurrent.futures.ThreadPoolExecutor(max_workers=DETECTION_WORKERS) as pool:
        futures = {
            pool.submit(
                detect_objects, frame_paths[frame_id], frame_id,
                orientation=orientations.get(frame_id, ""), transport=transport,
            ): frame_id
            for frame_id in wanted
        }
        for future in concurrent.futures.as_completed(futures):
            frame_id = futures[future]
            try:
                detections[frame_id] = future.result()
            except DetectionError as error:
                log.warning("no objects read from %s: %s", frame_id, error)
                failures.append(f"{frame_id}: {error}")
                continue
            if cache is not None:
                cache.put(frame_paths[frame_id], detections[frame_id], orientations.get(frame_id, ""))
    log.info("read %d photos, %d already known", len(wanted), len(cameras) - len(wanted))
    return detections, failures


def _not_already_read(
    cameras: list[PhotoCamera],
    frame_paths: dict[str, pathlib.Path],
    cache: DetectionCache | None,
    into: dict[str, list[Detection]],
    orientations: dict[str, str],
) -> list[str]:
    wanted = []
    for camera in cameras:
        stored = cache.get(
            frame_paths[camera.frame_id], camera.frame_id, orientations.get(camera.frame_id, "")
        ) if cache else None
        if stored is None:
            wanted.append(camera.frame_id)
        else:
            into[camera.frame_id] = stored
    return wanted


def _carve_all(
    cameras: list[PhotoCamera],
    detections: dict[str, list[Detection]],
    points: np.ndarray,
    buffers: dict[str, np.ndarray],
) -> list[Candidate]:
    candidates = []
    for camera in cameras:
        wanted = [one for one in detections.get(camera.frame_id, []) if not one.is_person]
        if not wanted:
            continue
        view = FrameView.of(points, camera, buffers[camera.frame_id])
        for detection in wanted:
            box = carve(view, detection)
            if box is not None and box.volume >= MIN_VOLUME:
                candidates.append(Candidate(detection=detection, box=box))
    return candidates


def _viewpoints(object_: DiscoveredObject, cameras: list[PhotoCamera]) -> int:
    """How many separate places this was seen from, rather than how many frames saw it."""
    positions = [
        camera.position for camera in cameras if camera.frame_id in set(object_.frame_ids)
    ]
    kept: list[np.ndarray] = []
    for position in positions:
        if all(float(np.linalg.norm(position - other)) >= APART for other in kept):
            kept.append(position)
    return len(kept)


def _worth_keeping(object_: DiscoveredObject, graph: SceneGraph, viewpoints: int) -> bool:
    if object_.name.strip().lower() in ALREADY_THE_ROOM:
        return False
    if viewpoints < MIN_VIEWS:
        return False
    if object_.box.volume < MIN_VOLUME or object_.box.floor_clearance > MAX_FLOOR_CLEARANCE:
        return False
    return not any(
        contained_fraction(object_.box, node) >= ALREADY_MEASURED
        for node in graph.nodes
        if node.kind == "object"
    )


def _node_for(object_: DiscoveredObject, graph: SceneGraph, viewpoints: int) -> SceneNode:
    resting = resting_parent(object_.box, graph)
    return SceneNode(
        id=_stable_id(graph.scan_id, object_),
        kind="object",
        label=object_.name.capitalize(),
        raw_category=object_.name.replace(" ", "_"),
        dimensions=object_.box.as_vec3(),
        transform=object_.box.as_transform(),
        quality="measured" if viewpoints >= CONFIDENT_VIEWS else "needs_another_look",
        movable=object_.movable,
        labeled_by="discovery",
        parent_id=resting,
        relation="rests_on" if resting is not None else None,
    )


def _stable_id(scan_id: uuid.UUID, object_: DiscoveredObject) -> uuid.UUID:
    """The same object in the same place keeps its id, so a rebuild does not orphan a move."""
    where = ",".join(f"{value:.2f}" for value in object_.box.centre)
    return uuid.uuid5(DISCOVERY_NAMESPACE, f"{scan_id}|{object_.name}|{where}")
