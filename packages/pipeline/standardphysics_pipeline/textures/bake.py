"""Project captured photos onto the room model and write a textured GLB."""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from PIL import Image
from standardphysics_contracts import NodeTextureCoverage, SceneGraph, TextureCoverage, bounds_the_room, lies_flat

from ..blender import BlenderError, _run, display_graph
from ..footprints import floor_polygon
from ..lidar import load_mesh, triangles_in_arkit_world
from .camera import CameraMetadataError, PhotoCamera, load_cameras
from .project import (
    MAX_EXPOSURE_POINTS,
    DepthBuffers,
    TopViews,
    TriangleBlocks,
    bilinear,
    exposure_gains,
    in_parallel,
    pad_gutters,
    rasterize_atlas,
    sample_surface,
    to_linear,
    to_srgb,
    triangle_depth_buffer,
    view_samples,
)
from .surface_materials import MaterialFill, material_key, room_materials


def _budget(name: str, fallback: int) -> int:
    """A frame budget, overridable for a long walk that deserves more of its own photos."""
    try:
        return max(1, int(os.environ[name]))
    except (KeyError, ValueError):
        return fallback


MAX_FRAMES = _budget("SP_TEXTURE_FRAMES", 192)
"""How many photos are projected onto the room.

A walk down one library floor stores over nine hundred photos, each with its
own measured pose, and forty-eight of them left three quarters of the surfaces
with no colour at all. The cost is one depth buffer per photo, so this trades
bake time for how much of the room comes back painted rather than blank.
"""

MAX_FRAME_CANDIDATES = _budget("SP_TEXTURE_FRAME_CANDIDATES", 4 * MAX_FRAMES)
"""How many photos are scored before the best are kept.

Thinning nine hundred photos to ninety-six before ranking them threw away the
views that would have reached the surfaces the chosen ones missed, because a
choice spread evenly through time is not spread evenly through the room.
"""
ATLAS_SIZE = 2048
MAX_ATLASES = 4
CHUNK_SIZE = 100_000
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_PIXELS = 24_000_000
MAX_IMAGE_EDGE = 2048


class TextureBakeError(RuntimeError):
    pass


@dataclass(frozen=True)
class BakeInputs:
    bake_graph: SceneGraph
    """Every node at its captured placement, with capture_to_room set."""
    poses_path: pathlib.Path
    frame_paths: dict[str, pathlib.Path]
    """Stored JPEG for each manifest frame id."""
    lidar_mesh_path: pathlib.Path | None
    out_dir: pathlib.Path
    """An empty directory that receives scene.glb and coverage-<atlas>.png."""
    materials_dir: pathlib.Path | None = None
    """This room's own generated materials, laid over the generic set, when it has any."""


@dataclass(frozen=True)
class BakeResult:
    glb_path: pathlib.Path
    coverage_mask_paths: list[pathlib.Path]
    coverage: TextureCoverage
    frames_used: int
    seconds: float


def bake_textures(inputs: BakeInputs) -> BakeResult:
    """Bake up to 48 calibrated photos into at most four 2048px base-color atlases.

    Blender makes exactly the viewer geometry and its node-local UVs. NumPy
    then projects photos in room coordinates, so clean geometry and optional
    LiDAR can reject occluded texels before Blender embeds the resulting maps.
    """
    started = time.monotonic()
    _validate_inputs(inputs)
    graph = display_graph(inputs.bake_graph)
    if graph.capture_to_room is None:
        raise TextureBakeError("bake graph has no capture_to_room transform")

    try:
        cameras = load_cameras(inputs.poses_path, inputs.frame_paths, graph.capture_to_room)
    except CameraMetadataError as error:
        raise TextureBakeError(str(error)) from error
    cameras = [
        camera
        for camera in cameras
        if inputs.frame_paths.get(camera.frame_id, pathlib.Path()).is_file()
    ]
    if not cameras:
        raise TextureBakeError("no stored frame has version 2 camera metadata")
    candidates = _evenly_spaced(cameras, MAX_FRAME_CANDIDATES)
    cameras, quality = _rank_cameras(candidates, inputs.frame_paths)
    images, cameras = _load_images(cameras, inputs.frame_paths)
    if not cameras:
        raise TextureBakeError("no readable stored JPEG matches a calibrated pose")

    with tempfile.TemporaryDirectory(prefix="standardphysics-textures-") as temp:
        work = pathlib.Path(temp)
        triangles_path, meta_path, blend_path = (
            work / "triangles.npz",
            work / "meta.json",
            work / "scene.blend",
        )
        _layout(graph, cameras, work, triangles_path, meta_path, blend_path)
        triangles = np.load(triangles_path)
        world = triangles["world"]
        uv = triangles["uv"]
        owners = triangles["owner"]
        face_colours = triangles["base_colour"] if "base_colour" in triangles else None
        meta = json.loads(meta_path.read_text())
        atlas_count = int(meta["atlas_count"])
        if atlas_count > MAX_ATLASES:
            raise TextureBakeError(f"layout requires {atlas_count} atlases (maximum is {MAX_ATLASES})")
        lidar = _lidar_triangles(inputs.lidar_mesh_path, graph.capture_to_room.m, work)
        clean_buffers = _depth_buffers(cameras, world)
        lidar_buffers = _depth_buffers(cameras, lidar) if len(lidar) else [None] * len(cameras)
        gains = _exposure_gains(world, cameras, images, clean_buffers, lidar_buffers)

        node_meta = meta["nodes"]
        fill = _material_fill(graph, node_meta, inputs.materials_dir)
        atlas_by_owner = np.asarray([node["atlas"] for node in node_meta], dtype=np.int32)
        base_by_owner = np.asarray([node["base_colour"] for node in node_meta], dtype=np.float32)
        if face_colours is None:
            face_colours = base_by_owner[owners]
        covered_by_owner = np.zeros(len(node_meta), dtype=np.int64)
        texels_by_owner = np.zeros(len(node_meta), dtype=np.int64)
        atlas_paths: list[pathlib.Path] = []
        masks: list[pathlib.Path] = []
        for atlas in range(atlas_count):
            selection = atlas_by_owner[owners] == atlas
            texels = rasterize_atlas(
                world[selection], uv[selection], owners[selection], ATLAS_SIZE,
                face_colours[selection],
            )
            atlas_image, covered, reachable = _bake_atlas(
                texels, cameras, images, clean_buffers, lidar_buffers, gains, quality, fill
            )
            for owner in np.unique(texels.owners):
                own = texels.owners == owner
                texels_by_owner[owner] += int((own & reachable).sum())
                covered_by_owner[owner] += int(covered[own].sum())
            atlas_path = work / f"atlas-{atlas}.png"
            mask_path = inputs.out_dir / f"coverage-{atlas}.png"
            Image.fromarray(atlas_image, "RGB").save(atlas_path, optimize=True)
            mask = np.zeros((ATLAS_SIZE, ATLAS_SIZE), dtype=np.uint8)
            mask[texels.rows[covered], texels.columns[covered]] = 255
            Image.fromarray(mask, "L").save(mask_path, optimize=True)
            atlas_paths.append(atlas_path)
            masks.append(mask_path)

        glb_path = inputs.out_dir / "scene.glb"
        _apply(blend_path, atlas_paths, masks, glb_path)
        coverage = _coverage(graph, node_meta, covered_by_owner, texels_by_owner)
    return BakeResult(
        glb_path=glb_path,
        coverage_mask_paths=masks,
        coverage=coverage,
        frames_used=len(cameras),
        seconds=time.monotonic() - started,
    )


def _validate_inputs(inputs: BakeInputs) -> None:
    if not inputs.poses_path.is_file():
        raise TextureBakeError(f"poses file does not exist: {inputs.poses_path}")
    if inputs.out_dir.exists() and any(inputs.out_dir.iterdir()):
        raise TextureBakeError(f"texture output directory must be empty: {inputs.out_dir}")
    inputs.out_dir.mkdir(parents=True, exist_ok=True)


def _is_an_opening(node) -> bool:
    """A doorway or a window: a hole in a surface rather than a surface.

    It is cut out of something that is already being drawn, so drawing it again
    lays a second skin over the same wall.
    """
    return bounds_the_room(node) and node.parent_id is not None


def _evenly_spaced(values: list[PhotoCamera], limit: int) -> list[PhotoCamera]:
    if len(values) <= limit:
        return values
    return [values[index] for index in np.linspace(0, len(values) - 1, limit, dtype=np.int64)]


def _rank_cameras(
    cameras: list[PhotoCamera], paths: dict[str, pathlib.Path]
) -> tuple[list[PhotoCamera], list[float]]:
    """Choose sharp, usable and spatially varied views without retaining full images."""
    scores = list(in_parallel(lambda camera: _view_score(camera, paths[camera.frame_id]), cameras))
    if len(cameras) <= MAX_FRAMES:
        return list(cameras), scores
    chosen = sorted(_most_novel(cameras, scores, MAX_FRAMES), key=lambda index: cameras[index].timestamp)
    return [cameras[index] for index in chosen], [scores[index] for index in chosen]


def _view_score(camera: PhotoCamera, path: pathlib.Path) -> float:
    thumbnail = _thumbnail(camera, path)
    sharpness = _sharpness(thumbnail)
    brightness = float(thumbnail.mean())
    exposure = max(0.05, 1.0 - abs(brightness - 0.55) / 0.55)
    # Even a deliberately plain wall photo still carries valid colour;
    # sharpness ranks views but cannot reduce a usable view to zero weight.
    return (0.05 + sharpness) * exposure


def _most_novel(cameras: list[PhotoCamera], scores: list[float], limit: int) -> list[int]:
    """Indices of the views picked one at a time by score times how unlike every earlier pick each is.

    A view's novelty is its smallest difference from any pick so far, so it
    only changes when a new pick lands near it. Keeping that running minimum
    and folding in each new pick once is the same choice as comparing every
    candidate with every pick each round, which on a library floor was
    hundreds of thousands of comparisons per pick.
    """
    positions = [camera.position for camera in cameras]
    forwards = [camera.forward for camera in cameras]
    score = np.asarray(scores, dtype=np.float64)
    novelty = np.full(len(cameras), np.inf)
    available = np.ones(len(cameras), dtype=bool)
    chosen: list[int] = []
    while available.any() and len(chosen) < limit:
        value = score if not chosen else score * (0.35 + np.minimum(novelty, 1.0))
        best = int(np.argmax(np.where(available, value, -np.inf)))
        chosen.append(best)
        available[best] = False
        for index in np.flatnonzero(available):
            position = float(np.linalg.norm(positions[index] - positions[best]))
            direction = 1.0 - float(np.dot(forwards[index], forwards[best]))
            novelty[index] = min(novelty[index], max(position / 1.0, direction))
    return chosen


def _thumbnail(camera: PhotoCamera, path: pathlib.Path) -> np.ndarray:
    with Image.open(path) as image:
        _validate_image(camera, path, image)
        image.thumbnail((256, 256), Image.Resampling.BILINEAR)
        return np.asarray(image.convert("L"), dtype=np.float32) / 255.0


def _sharpness(image: np.ndarray) -> float:
    if image.shape[0] < 3 or image.shape[1] < 3:
        return 0.0
    laplacian = -4 * image[1:-1, 1:-1] + image[:-2, 1:-1] + image[2:, 1:-1] + image[1:-1, :-2] + image[1:-1, 2:]
    return float(np.var(laplacian))


class _Photos(Sequence):
    """The chosen photos, each read from disk when a pass uses it rather than all held at once.

    A bake holds up to 192 photos, and as linear floats at full size they came
    to over six gigabytes, which is more memory than a small server has; the
    bake of a library floor was killed for it. Decoding is deterministic, so a
    photo read twice is the same pixels both times and the bake is unchanged.
    """

    def __init__(self, cameras: list[PhotoCamera], paths: dict[str, pathlib.Path]):
        self.cameras, self.paths = cameras, paths

    def __len__(self) -> int:
        return len(self.cameras)

    def __getitem__(self, index: int) -> np.ndarray:
        return _decoded(self.cameras[index], self.paths[self.cameras[index].frame_id])


def _decoded(camera: PhotoCamera, path: pathlib.Path) -> np.ndarray:
    try:
        with Image.open(path) as image:
            _validate_image(camera, path, image)
            image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
            rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    except (OSError, KeyError, ValueError) as error:
        raise TextureBakeError(f"unreadable photo {camera.frame_id}: {error}") from error
    return to_linear(rgb).astype(np.float32)


def _load_images(
    cameras: list[PhotoCamera], paths: dict[str, pathlib.Path]
) -> tuple[_Photos, list[PhotoCamera]]:
    """Every usable photo checked and measured once, and a list that reads each again when it is needed."""
    usable = []
    for camera in cameras:
        width, height = _decoded_size(camera, paths[camera.frame_id])
        if height < 2 or width < 2:
            continue
        usable.append(camera.resized(width, height))
    originals = {camera.frame_id: camera for camera in cameras}
    return _Photos([originals[camera.frame_id] for camera in usable], paths), usable


def _decoded_size(camera: PhotoCamera, path: pathlib.Path) -> tuple[int, int]:
    """The width and height `_decoded` gives this photo, read from its header rather than by decoding it.

    PIL sizes a thumbnail from the source dimensions alone, so a blank image of
    the same size shrinks to the same size as the photo would.
    """
    try:
        with Image.open(path) as image:
            _validate_image(camera, path, image)
            stand_in = Image.new("L", image.size)
    except (OSError, KeyError, ValueError) as error:
        raise TextureBakeError(f"unreadable photo {camera.frame_id}: {error}") from error
    stand_in.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
    return stand_in.size


def _validate_image(camera: PhotoCamera, path: pathlib.Path, image: Image.Image) -> None:
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise TextureBakeError(f"photo {camera.frame_id} exceeds {MAX_SOURCE_BYTES} byte limit")
    if image.width * image.height > MAX_SOURCE_PIXELS:
        raise TextureBakeError(f"photo {camera.frame_id} exceeds {MAX_SOURCE_PIXELS} pixel limit")
    if (image.width, image.height) != (camera.width, camera.height):
        raise TextureBakeError(
            f"photo {camera.frame_id} is {image.width}x{image.height}, "
            f"but pose metadata says {camera.width}x{camera.height}"
        )


def _depth_buffers(cameras: list[PhotoCamera], triangles: np.ndarray) -> list[np.ndarray]:
    """Each photo's depth buffer, drawn from only the faces in cubes its frame reaches, on every core."""
    blocks = TriangleBlocks(triangles)
    return list(in_parallel(lambda camera: triangle_depth_buffer(camera, triangles[blocks.seen_by(camera)]), cameras))


def _exposure_gains(world, cameras, images, clean_buffers, lidar_buffers) -> np.ndarray:
    points, normals = sample_surface(world, 0.2, seed=7)
    if len(points) > MAX_EXPOSURE_POINTS:
        indices = np.linspace(0, len(points) - 1, MAX_EXPOSURE_POINTS, dtype=np.int64)
        points, normals = points[indices], normals[indices]
    observations = []
    for camera, image, clean, lidar in zip(cameras, images, clean_buffers, lidar_buffers):
        samples = view_samples(DepthBuffers(camera, clean, lidar), points, normals, 1.0)
        accepted = np.flatnonzero(samples.accepted)
        observations.append((accepted, bilinear(image, samples.u[accepted], samples.v[accepted])))
    return exposure_gains(observations, len(cameras), len(points))


def _layout(
    graph: SceneGraph,
    cameras: list[PhotoCamera],
    work: pathlib.Path,
    triangles: pathlib.Path,
    meta: pathlib.Path,
    blend: pathlib.Path,
) -> None:
    graph_path, floors_path, density_path = work / "graph.json", work / "floors.json", work / "density.json"
    graph_path.write_text(graph.model_dump_json())
    floors_path.write_text(json.dumps({
        str(node.id): floor_polygon(node) for node in graph.nodes if lies_flat(node)
    }))
    detail = min(512.0, max(48.0, min(min(camera.width, camera.height) for camera in cameras) / 3.0))
    density_path.write_text(json.dumps({
        str(node.id): detail
        for node in graph.nodes
        if not _is_an_opening(node)
    }))
    try:
        output = _run("texture_scene.py", [
            "layout", "--graph", str(graph_path), "--floors", str(floors_path),
            "--density", str(density_path), "--blend", str(blend),
            "--triangles", str(triangles), "--meta", str(meta),
        ])
    except (BlenderError, FileNotFoundError) as error:
        raise TextureBakeError(f"could not build texture UV layout: {error}") from error
    if "TEXTURE_LAYOUT_WRITTEN" not in output or not triangles.is_file() or not meta.is_file():
        raise TextureBakeError("Blender did not produce a texture UV layout")


def _lidar_points(path: pathlib.Path | None, capture_to_room: list[float], work: pathlib.Path) -> np.ndarray:
    """Compatibility helper for callers that only need a bounded LiDAR cloud."""
    triangles = _lidar_triangles(path, capture_to_room, work)
    if not len(triangles):
        return np.empty((0, 3), dtype=np.float32)
    return triangles.reshape(-1, 3)


def _lidar_triangles(path: pathlib.Path | None, capture_to_room: list[float], work: pathlib.Path) -> np.ndarray:
    if path is None:
        return np.empty((0, 3), dtype=np.float32)
    if not path.is_file():
        raise TextureBakeError(f"LiDAR mesh does not exist: {path}")
    try:
        mesh = load_mesh(path)
        if mesh is not None:
            triangles = triangles_in_arkit_world(mesh)
        elif path.suffix.lower() == ".npz":
            archive = np.load(path)
            if "triangles" not in archive:
                raise TextureBakeError("LiDAR NPZ must contain triangle faces")
            triangles = archive["triangles"]
        else:
            extracted = work / "lidar-points.npz"
            output = _run("texture_scene.py", ["points", "--mesh", str(path), "--out", str(extracted)])
            if "LIDAR_TRIANGLES_WRITTEN" not in output:
                raise TextureBakeError("Blender did not extract LiDAR triangles")
            triangles = np.load(extracted)["triangles"]
    except (OSError, KeyError, BlenderError, FileNotFoundError, ValueError) as error:
        raise TextureBakeError(f"could not read LiDAR mesh: {error}") from error
    triangles = np.asarray(triangles, dtype=np.float32).reshape(-1, 3, 3)
    if not len(triangles):
        return triangles
    matrix = np.asarray(capture_to_room, dtype=np.float32).reshape(4, 4)
    triangles = triangles @ matrix[:3, :3].T + matrix[:3, 3]
    return triangles


def _material_fill(graph: SceneGraph, node_meta: list[dict], materials_dir: pathlib.Path | None) -> MaterialFill:
    """Material keys indexed the way the layout indexes its owners."""
    by_id = {str(node.id): node for node in graph.nodes}
    keys = [material_key(by_id[node["id"]]) if node["id"] in by_id else "" for node in node_meta]
    return MaterialFill(keys, room_materials(materials_dir))


def _bake_atlas(
    texels, cameras, images, clean_buffers, lidar_buffers, gains, quality, fill: MaterialFill
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The painted atlas, which texels received colour, and which could ever have.

    Texels no photo reached take their owner's generated material, or its plain
    base colour when it has none.
    """
    image = np.zeros((ATLAS_SIZE, ATLAS_SIZE, 3), dtype=np.float32)
    views = TopViews(len(texels))
    reachable = np.zeros(len(texels), dtype=bool)
    for camera, photo, clean, lidar, gain, view_quality in zip(cameras, images, clean_buffers, lidar_buffers, gains, quality):
        buffers = DepthBuffers(camera, clean, lidar)
        for start in range(0, len(texels), CHUNK_SIZE):
            stop = min(len(texels), start + CHUNK_SIZE)
            samples = view_samples(buffers, texels.positions[start:stop], texels.normals[start:stop], view_quality)
            reachable[start:stop] |= samples.faced
            accepted = np.flatnonzero(samples.accepted)
            if len(accepted):
                colors = np.clip(bilinear(photo, samples.u[accepted], samples.v[accepted]) * gain, 0.0, 1.0)
                views.add(accepted + start, samples.weight[accepted], colors)
            disagreement = np.flatnonzero(samples.disagreed)
            if len(disagreement):
                views.note_disagreement(disagreement + start)
    colors, covered = views.resolve()
    painted = to_linear(texels.base_colours).astype(np.float32)
    painted[covered] = colors[covered]
    image[texels.rows, texels.columns] = fill.apply(painted, covered, texels.positions, texels.normals, texels.owners)
    filled = np.zeros((ATLAS_SIZE, ATLAS_SIZE), dtype=bool)
    filled[texels.rows, texels.columns] = True
    image = pad_gutters(image, filled)
    return np.rint(to_srgb(image) * 255).astype(np.uint8), covered, reachable


def _coverage(
    graph: SceneGraph, node_meta: list[dict], covered: np.ndarray, total: np.ndarray
) -> TextureCoverage:
    """How much of what the walk could have photographed it did photograph.

    `total` counts only texels some camera faced from inside its frame. The
    back of a wall, the underside of a table and the far side of a cabinet are
    not missing colour, because standing in the room there was never a shot to
    take. Counting them made a good bake read as 14 per cent covered, which
    told the owner to rescan a room that had been scanned properly.
    """
    by_id = {node["id"]: index for index, node in enumerate(node_meta)}
    entries = []
    needs = []
    for node in graph.nodes:
        index = by_id.get(str(node.id))
        fraction = float(covered[index] / total[index]) if index is not None and total[index] else 0.0
        entries.append(NodeTextureCoverage(node_id=node.id, textured_fraction=fraction))
        if index is not None and fraction < 0.6:
            needs.append(node.id)
    eligible = total.sum()
    return TextureCoverage(
        textured_fraction=float(covered.sum() / eligible) if eligible else 0.0,
        nodes=entries,
        needs_another_view=needs,
    )


def _apply(
    blend: pathlib.Path, atlases: list[pathlib.Path], masks: list[pathlib.Path], out: pathlib.Path
) -> None:
    try:
        output = _run("texture_scene.py", [
            "apply", "--blend", str(blend), "--atlases", *(str(path) for path in atlases),
            "--coverage", *(str(path) for path in masks), "--out", str(out),
        ])
    except (BlenderError, FileNotFoundError) as error:
        raise TextureBakeError(f"could not export textured GLB: {error}") from error
    if "GLB_WRITTEN" not in output or not out.is_file():
        raise TextureBakeError("Blender did not produce a textured GLB")
