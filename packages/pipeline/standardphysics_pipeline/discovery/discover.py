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
import hashlib
import json
import logging
import pathlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import numpy as np
from standardphysics_contracts import SceneGraph, SceneNode, bounds_the_room

from ..lidar import LidarMeshError, room_cloud, room_faces
from ..textures.camera import CameraMetadataError, PhotoCamera, load_cameras
from ..textures.project import depth_buffer, evenly_spread
from . import taxonomy
from .boxes import claimed_by_any, contained_fraction, resting_parent, structure_points
from .cache import DetectionCache
from .carve import FrameView, carve
from .crops import save_crop
from .detect import (
    Detection,
    DetectionError,
    ModelRequestInfo,
    Transport,
    answer_identity,
    detect_objects,
)
from .extent import MeshViews, measured_on_the_mesh
from .grow import grown, regions_of, seen_from
from .merge import Candidate, DiscoveredObject, merge_candidates
from .mesh_surfaces import SegmentedSurfaces, segment_surfaces
from .people import PeopleRemoval, PersonVolume, without_people
from .placement import (
    at_its_surface,
    part_of_a_scanned_piece,
    seated,
    seen_through_the_shell,
    standing_on_the_floor,
)
from .reconcile import reconcile_outlets
from .second_look import Photos, second_look
from .semantic_corrections import apply_secondary_semantic_corrections, is_work_surface
from .surface_attach import attach_detection_to_surface
from .walk_sampling import worth_reading
from .worktops import measure_worktops

log = logging.getLogger(__name__)

FRAME_LIMIT = 400
"""Every keyframe of a normal walk that the walk sampler keeps. A frame nobody reads is a person left in
the mesh and an object that was never there: on a real 110-second capture,
sampling 24 of 218 frames found half the laptops and a quarter of the people."""
DETECTION_WORKERS = 16
"""Photos in flight at once. The Fireworks account allows 87,890 generated
tokens a minute and a photo read without reasoning costs about 370 of them in
about 5.5 s. On Share-Tea sixteen at once read 400 photos in 110 s at about
81,000 tokens a minute with no rate limit hit; twenty-eight at once took 98 s
and drew 78 rate limits, all of them waited out. Past the budget, width buys
nothing but waiting."""
RETRY_WORKERS = 4
"""Photos in flight when asking again for the ones the first pass could not read."""
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
    crop_dir: pathlib.Path | None = None
    """Where source-resolution evidence crops of surface targets are written.

    Populated through the scan's own crops directory so the existing crop
    route can serve them. When None, no crops are written and observations
    carry no resolvable crop reference.
    """


@dataclass
class DiscoveryResult:
    nodes: list[SceneNode] = field(default_factory=list)
    objects: list[DiscoveredObject] = field(default_factory=list)
    frames_read: int = 0
    people_points_removed: int = 0
    frames_with_people: int = 0
    failures: list[str] = field(default_factory=list)
    """Frames the vision model could not read. Never silent: a dropped frame is a smaller answer."""
    model_requests: list[ModelRequestInfo] = field(default_factory=list)
    surfaces: SegmentedSurfaces | None = None
    """Every actual detector request this run made, for the evidence trail."""

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
    requests: list[ModelRequestInfo] = []
    orientations = _orientations(inputs.poses_path)
    detections, failures = _detect_all(
        cameras, inputs.frame_paths, transport, _cache_for(inputs), orientations, recorded=requests,
    )
    buffers = {camera.frame_id: depth_buffer(camera, points) for camera in cameras}
    views = [(camera, detections.get(camera.frame_id, []), buffers[camera.frame_id]) for camera in cameras]
    removal = without_people(points, graph, views)
    worktops = measure_worktops(graph, removal.points)
    graph = _with_replaced(graph, worktops)
    renamed = _semantic_corrections(graph, detections, cameras)
    graph = _with_replaced(graph, renamed)
    kept = _carved_objects(graph, cameras, detections, removal, MeshViews(points, views))
    looked = second_look([object_ for object_, _ in kept], Photos(cameras, inputs.frame_paths, orientations),
                         transport=transport, cache_dir=inputs.cache_dir)
    kept = [(object_, viewpoints) for object_, (_, viewpoints) in zip(looked, kept)]
    objects = [object_ for object_, _ in kept]
    carved_nodes = [_node_for(object_, graph, viewpoints) for object_, viewpoints in kept]
    attached_nodes = _attached_targets(inputs, graph, cameras, detections, buffers)

    existing_attachments = [n for n in inputs.graph.nodes if n.attachment is not None]
    reconciled_nodes = reconcile_outlets(existing_attachments + attached_nodes, {c.frame_id: c for c in cameras})

    discovery_nodes = {node.id: node for node in [*worktops, *renamed, *carved_nodes, *reconciled_nodes]}

    discovered = list(discovery_nodes.values())
    replaced = {node.id: node for node in discovered}
    updated_graph = graph.model_copy(update={
        "nodes": [replaced.get(node.id, node) for node in graph.nodes]
        + [node for node in discovered if node.id not in {existing.id for existing in graph.nodes}],
    })
    surfaces = segment_surfaces(room_faces(inputs.lidar_mesh_path, graph.capture_to_room), updated_graph, graph)
    return DiscoveryResult(
        nodes=list(discovery_nodes.values()),
        surfaces=surfaces,
        objects=objects,
        frames_read=len(cameras) - len(failures),
        people_points_removed=removal.removed,
        frames_with_people=removal.frames_with_people,
        failures=failures,
        model_requests=requests,
    )


def _with_replaced(graph: SceneGraph, nodes: list[SceneNode]) -> SceneGraph:
    """The graph with these nodes swapped in by id, and any it did not hold added."""
    replacing = {node.id: node for node in nodes}
    kept = [replacing.pop(node.id, node) for node in graph.nodes]
    return graph.model_copy(update={"nodes": [*kept, *replacing.values()]})


def _carved_objects(
    graph: SceneGraph,
    cameras: list[PhotoCamera],
    detections: dict[str, list[Detection]],
    removal: PeopleRemoval,
    mesh: MeshViews,
) -> list[tuple[DiscoveredObject, int]]:
    """Every object worth a node, with how many separate places it was seen from.

    Carving looks through a depth buffer built from the mesh with the people
    already taken out. A customer standing at the till leaves a body in the
    mesh, and a buffer built with it hides the till behind that body in every
    frame of the walk, including the frames the detector saw the till in
    because the customer had stepped away. Whatever is still carved where a
    person stood is a leftover piece of them, not an object.

    A piece of something big grows to the whole mesh region it belongs to
    before it is judged. The small objects kept are then measured on the mesh
    itself, so their heights do not rest on which rectangles this run's
    detector drew.
    """
    points, people = removal.points, removal.volumes
    clear_view = {camera.frame_id: depth_buffer(camera, points) for camera in cameras}
    unclaimed = points[~claimed_by_any(points, graph)]
    candidates = _carve_all(cameras, detections, unclaimed, clear_view)
    found = [
        (object_, _viewpoints(object_, cameras, grew, clear_view), grew)
        for object_, grew in grown(merge_candidates(candidates), regions_of(unclaimed, graph))
    ]
    loose = points[~structure_points(points, graph)]
    kept = [(object_, viewpoints) for object_, viewpoints, grew in found
            if _worth_keeping(object_, graph, viewpoints, cameras, people, loose, grew=grew)]
    measured = measured_on_the_mesh([object_ for object_, _ in kept], mesh.loose_near([o for o, _ in kept], graph, people))
    return [
        (replace(standing, box=seated(standing.box, graph, loose)), viewpoints)
        for object_, (_, viewpoints) in zip(measured, kept)
        for standing in [_standing_at_its_surface(object_, graph, loose)]
        if standing is not None
    ]


def _standing_at_its_surface(object_: DiscoveredObject, graph: SceneGraph, loose: np.ndarray) -> DiscoveredObject | None:
    standing = standing_on_the_floor(object_, graph, loose)
    return None if standing is None else at_its_surface(standing, graph, loose)


def _attached_targets(
    inputs: DiscoveryInputs,
    graph: SceneGraph,
    cameras: list[PhotoCamera],
    detections: dict[str, list[Detection]],
    buffers: dict[str, np.ndarray],
) -> list[SceneNode]:
    """Outlets and televisions placed on the measured surface each photo shows them on."""
    attached: list[SceneNode] = []
    for camera in cameras:
        for det in detections.get(camera.frame_id, []):
            if not det.is_attachable_target:
                continue
            image_url = None
            if inputs.crop_dir is not None:
                image_url = save_crop(inputs.frame_paths[camera.frame_id], det.frame_id, det.box, inputs.crop_dir)
            _, node = attach_detection_to_surface(
                det, camera, graph, depth_buffer=buffers.get(camera.frame_id), image_url=image_url,
            )
            attached.append(node)
    return attached


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
    return evenly_spread(worth_reading(stored), FRAME_LIMIT)


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


def known_detections(
    frame_paths: dict[str, pathlib.Path], poses_path: pathlib.Path, cache_dir: pathlib.Path,
) -> dict[str, list[Detection]]:
    """What the model already said about each photo, read from the cache and never asked.

    A texture build uses this to find the people in its photos without spending
    a request; a photo discovery has not read yet simply has no entry.
    """
    cache = DetectionCache(cache_dir, answer_identity())
    orientations = _orientations(poses_path)
    known = {}
    for frame_id, path in frame_paths.items():
        stored = cache.get(path, frame_id, orientations.get(frame_id, ""))
        if stored is not None:
            known[frame_id] = stored
    return known


def detections_digest(cache_dir: pathlib.Path) -> str:
    """A fingerprint of every stored answer, so a build made before discovery ran is not reused."""
    digest = hashlib.sha256()
    for entry in sorted(pathlib.Path(cache_dir).glob("*.json")):
        digest.update(entry.name.encode())
        digest.update(entry.read_bytes())
    return digest.hexdigest() if pathlib.Path(cache_dir).is_dir() else ""


def _cache_for(inputs: DiscoveryInputs) -> DetectionCache | None:
    if inputs.cache_dir is None:
        return None
    return DetectionCache(inputs.cache_dir, answer_identity())


def _detect_all(
    cameras: list[PhotoCamera],
    frame_paths: dict[str, pathlib.Path],
    transport: Transport | None,
    cache: DetectionCache | None,
    orientations: dict[str, str],
    *,
    recorded: list[ModelRequestInfo] | None = None,
) -> tuple[dict[str, list[Detection]], list[str]]:
    """What the model says about every photo, asked a second time for any the first pass lost.

    A frame that fails is not an empty frame: the objects only it saw would
    silently never exist. So the photos the first pass could not read are asked
    for again, fewer at once, after the rest of the walk has finished with the
    host. Only what still fails then is reported, and never silently.
    """
    detections: dict[str, list[Detection]] = {}
    wanted = _not_already_read(cameras, frame_paths, cache, detections, orientations)
    ask = _Asker(frame_paths, transport, cache, orientations, recorded, detections)
    unread = ask.all(wanted, DETECTION_WORKERS)
    if unread:
        log.info("asking again for %d photos the first pass could not read", len(unread))
        unread = ask.all(list(unread), RETRY_WORKERS)
    for frame_id, error in unread.items():
        log.warning("no objects read from %s: %s", frame_id, error)
    log.info("read %d photos, %d already known", len(wanted), len(cameras) - len(wanted))
    return detections, [f"{frame_id}: {error}" for frame_id, error in unread.items()]


@dataclass
class _Asker:
    """One detection pass over a set of photos, filling `into` and the cache as answers arrive."""

    frame_paths: dict[str, pathlib.Path]
    transport: Transport | None
    cache: DetectionCache | None
    orientations: dict[str, str]
    recorded: list[ModelRequestInfo] | None
    into: dict[str, list[Detection]]

    def all(self, frame_ids: list[str], workers: int) -> dict[str, DetectionError]:
        """The photos that could not be read, with why."""
        unread: dict[str, DetectionError] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.one, frame_id): frame_id for frame_id in frame_ids}
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except DetectionError as error:
                    unread[futures[future]] = error
        return unread

    def one(self, frame_id: str) -> None:
        orientation = self.orientations.get(frame_id, "")
        found = detect_objects(
            self.frame_paths[frame_id], frame_id,
            orientation=orientation, transport=self.transport, recorded=self.recorded,
        )
        self.into[frame_id] = found
        if self.cache is not None:
            self.cache.put(self.frame_paths[frame_id], found, orientation)


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
        wanted = [one for one in detections.get(camera.frame_id, []) if not one.is_person and not one.is_attachable_target]
        if not wanted:
            continue
        view = FrameView.of(points, camera, buffers[camera.frame_id])
        for detection in wanted:
            box = carve(view, detection)
            if box is not None and box.volume >= MIN_VOLUME:
                candidates.append(Candidate(detection=detection, box=box))
    return candidates


def _viewpoints(
    object_: DiscoveredObject,
    cameras: list[PhotoCamera],
    grew: bool = False,
    buffers: dict[str, np.ndarray] | None = None,
) -> int:
    """How many separate places this was seen from, rather than how many frames saw it.

    An object grown to its whole region is measured by the mesh, so every place
    that saw the region counts, not only the photos that named it.
    """
    if grew:
        return _apart(seen_from(object_.box.points, cameras, buffers or {}))
    return _apart([camera.position for camera in cameras if camera.frame_id in set(object_.frame_ids)])


def _apart(positions: list[np.ndarray]) -> int:
    kept: list[np.ndarray] = []
    for position in positions:
        if all(float(np.linalg.norm(position - other)) >= APART for other in kept):
            kept.append(position)
    return len(kept)


def _worth_keeping(
    object_: DiscoveredObject,
    graph: SceneGraph,
    viewpoints: int,
    cameras: Sequence[PhotoCamera] = (),
    people: Sequence[PersonVolume] = (),
    loose: np.ndarray | None = None,
    *,
    grew: bool = False,
) -> bool:
    """Whether a found object is worth a node.

    One place is enough for an object grown to its whole region: the rule that
    asks for more exists to drop fragments of something else, and a whole
    region is not one. It still comes back asking for another look.
    """
    if object_.name.strip().lower() in ALREADY_THE_ROOM or viewpoints < (1 if grew else _views_needed(object_)):
        return False
    if object_.box.volume < MIN_VOLUME or object_.box.floor_clearance > MAX_FLOOR_CLEARANCE:
        return False
    return not (
        _already_measured(object_, graph)
        or part_of_a_scanned_piece(object_, graph, loose)
        or seen_through_the_shell(object_.box, graph, _positions(object_, cameras))
        or any(person.holds(object_.box) for person in people)
    )


def _views_needed(object_: DiscoveredObject) -> int:
    """Separate places an object must be named from before it becomes a node.

    A table or counter RoomPlan did not box is, more often than not, a mash of
    what stands along a wall: on Share-Tea the photos called a bench, the bar
    ledge above it and a kiosk behind both a counter from two places, and it
    came out a floor-standing counter 80 inches tall. A real one is big
    enough to be named from wherever the walk passes it.
    """
    return CONFIDENT_VIEWS if is_work_surface(object_.name) else MIN_VIEWS


def _already_measured(object_: DiscoveredObject, graph: SceneGraph) -> bool:
    return any(
        contained_fraction(object_.box, node) >= ALREADY_MEASURED
        for node in graph.nodes
        if not bounds_the_room(node)
    )


def _positions(object_: DiscoveredObject, cameras: Sequence[PhotoCamera]) -> np.ndarray:
    """Where the phone stood for each photo of this object."""
    seen = set(object_.frame_ids)
    return np.asarray([camera.position for camera in cameras if camera.frame_id in seen], dtype=np.float64)


def _semantic_corrections(
    graph: SceneGraph,
    detections: dict[str, list[Detection]],
    cameras: list[PhotoCamera],
) -> list[SceneNode]:
    """Scanned nodes relabelled by photographic evidence, and new whiteboards.

    Only what changed is returned, so the caller replaces graph nodes by id
    without ever mutating an untouched one. It runs before carving, so a
    storage box the photos show is a counter is a counter by the time the
    counter's own lid is carved and has to be recognised as part of it.
    Carved objects are not revoted: they were named by these same detections.
    """
    corrected = apply_secondary_semantic_corrections(graph, _relevant_detections(detections), cameras)
    by_id = {node.id: node for node in graph.nodes}
    return [node for node in corrected.nodes if node.id not in by_id or by_id[node.id] != node]


def _relevant_detections(detections: dict[str, list[Detection]]) -> dict[str, list[Detection]]:
    """The findings the correction pass weighs: the relabel targets, and the furniture that argues against them.

    A chair must be able to vote for being a chair, or every table box it
    stands inside outvotes it.
    """
    return {
        frame_id: [one for one in found if one.class_key != taxonomy.PERSON and one.category == "object"]
        for frame_id, found in detections.items()
    }


def _node_for(object_: DiscoveredObject, graph: SceneGraph, viewpoints: int) -> SceneNode:
    resting = resting_parent(object_.box, graph)
    return SceneNode(
        id=_stable_id(graph.scan_id, object_),
        kind="object",
        label=object_.name.capitalize(),
        raw_category=object_.name.replace(" ", "_"),
        dimensions=object_.box.as_vec3(),
        transform=object_.box.as_transform(),
        quality="measured" if viewpoints >= CONFIDENT_VIEWS and object_.name_settled else "needs_another_look",
        movable=object_.movable and not taxonomy.is_fixture_name(object_.name),
        labeled_by="discovery",
        parent_id=resting,
        relation="rests_on" if resting is not None else None,
    )


def _stable_id(scan_id: uuid.UUID, object_: DiscoveredObject) -> uuid.UUID:
    """The same object in the same place keeps its id, so a rebuild does not orphan a move."""
    where = ",".join(f"{value:.2f}" for value in object_.box.centre)
    return uuid.uuid5(DISCOVERY_NAMESPACE, f"{scan_id}|{object_.name}|{where}")
