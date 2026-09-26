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
from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np
from standardphysics_contracts import SceneGraph

from ..lidar import load_mesh
from .camera import PhotoCamera, load_cameras
from .project import (
    MAX_EXPOSURE_POINTS,
    PointBlocks,
    TopViews,
    bilinear,
    depth_buffer,
    evenly_spread,
    exposure_gains,
    in_parallel,
    to_linear,
    to_srgb,
)
from .stages import timed

MAX_PHOTOS = 60
"""Photos read for colour. More photos raise coverage; this is where the gain flattens."""
MAX_PHOTO_EDGE = 1600
MAX_GEOMETRY_PHOTOS = 400
"""Photos asked which vertices are people and which are seen through, before anything is coloured.

Both are votes over the photos that frame a point, and a walk's neighbouring
video frames are nearly the same view: a merged floor's 3,524 photos took a
small server over twelve minutes for these two steps alone. Four hundred spread
evenly through the walk still give every surface several distinct views.
"""

SEEN_TOLERANCE = 0.05
"""How close to the nearest scanned surface a vertex must be to count as seen."""
MIN_FACING = 0.20
BORDER_FALLOFF_PIXELS = 24.0
BLEND_SHARPNESS = 4.0
"""Power applied to view weights before blending. A view twice as good as the
next contributes sixteen times as much, so detail stays from the best photo and
only near-ties, which is where the best photo changes, mix."""
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
    sources: np.ndarray | None = None
    """Per-vertex source frame ID of the best view (object dtype), None when unseen."""
    inferred: np.ndarray | None = None
    """Whether each vertex was added to patch a hole rather than scanned; None when nothing was."""
    sheet_patches: np.ndarray | None = None
    """The added vertices that patch a wall or floor, as opposed to closing a hole in an object."""
    mirror_source: np.ndarray | None = None
    """For a vertex reflected in to complete an object, the vertex it mirrors; -1 otherwise."""

    @property
    def scanned(self) -> np.ndarray:
        return ~self.inferred if self.inferred is not None else np.ones(len(self.seen), dtype=bool)

    @property
    def painted_fraction(self) -> float:
        """The share of scanned vertices a photo reached. Patches never count."""
        seen = self.seen[self.scanned]
        return float(seen.mean()) if len(seen) else 0.0


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
    camera: PhotoCamera,
    vertices: np.ndarray,
    normals: np.ndarray,
    buffer: np.ndarray,
    mask: np.ndarray | None = None,
    slope_aware: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """How good this camera's view of each vertex is, and where to sample it.

    ``mask`` must already be resampled to the depth-buffer grid (static
    region = 1); samples outside the static region never paint.

    With ``slope_aware``, the depth a point may sit behind the buffer grows
    with how steeply the camera sees its surface. One buffer pixel covers a
    patch of surface whose depth changes by its width times the slope, so on a
    wall seen at an angle a fixed tolerance hid the far part of every pixel
    behind its own near part, and the wall came out speckled.
    """
    columns, rows, depth = camera.project(vertices)
    framed = np.flatnonzero(
        (depth > 0.2) & (columns >= 0) & (columns <= camera.width - 1) & (rows >= 0) & (rows <= camera.height - 1)
    )
    weight = np.zeros(len(vertices), dtype=np.float64)
    weight[framed] = _framed_weights(
        camera, vertices[framed], PickedRows(normals, framed), columns[framed], rows[framed], depth[framed], buffer, mask, slope_aware,
    )
    return weight, columns, rows


class PickedRows:
    """The rows of an array chosen through an index, gathered only for the rows asked for.

    A photo reaches a million texels and paints about one in a hundred, so
    gathering every normal up front copied a million rows to use ten thousand.
    """

    def __init__(self, rows, index: np.ndarray):
        self.rows, self.index = rows, index

    def __getitem__(self, chosen):
        return self.rows[self.index[chosen]]


def _framed_weights(camera, vertices, normals, columns, rows, depth, buffer, mask, slope_aware) -> np.ndarray:
    """The view weight of points already inside the frame and in front of the camera.

    Only these are worth the arithmetic: a photo's cubes reach well past its
    frame, and every point outside it weighs nothing whatever its angle. Of
    those in frame, most on a library floor sit behind a shelf or a wall, so
    the depth buffer is asked first with the widest tolerance any point could
    have, and the exact weight is worked out only for the points it lets through.
    """
    height, width = buffer.shape
    pixel_rows = np.clip(np.rint(rows * height / camera.height).astype(np.int64), 0, height - 1)
    pixel_columns = np.clip(np.rint(columns * width / camera.width).astype(np.int64), 0, width - 1)
    nearest = buffer[pixel_rows, pixel_columns]
    weight = np.zeros(len(vertices), dtype=np.float64)
    near = np.flatnonzero(~np.isfinite(nearest) | (depth <= nearest + _widest_tolerance(camera, width, depth, slope_aware)))
    toward = camera.position[None, :] - vertices[near]
    distance = np.linalg.norm(toward, axis=1)
    facing = np.einsum("ij,ij->i", normals[near], toward) / np.maximum(distance, 1e-9)
    tolerance = _seen_tolerance(camera, width, depth[near], facing, slope_aware)
    unhidden = ~np.isfinite(nearest[near]) | (depth[near] <= nearest[near] + tolerance)
    border = np.clip(
        np.minimum.reduce([columns[near], rows[near], camera.width - 1 - columns[near], camera.height - 1 - rows[near]])
        / BORDER_FALLOFF_PIXELS, 0.0, 1.0,
    )
    weight[near] = np.where((facing > MIN_FACING) & unhidden, facing ** 2 / np.maximum(distance, 0.5) * border, 0.0)
    if mask is not None:
        weight = np.where(mask[pixel_rows, pixel_columns] >= 0.5, weight, 0.0)
    return weight


def _widest_tolerance(camera: PhotoCamera, buffer_width: int, depth: np.ndarray, slope_aware: bool):
    """The most `_seen_tolerance` allows any point at this depth that faces the camera at least MIN_FACING.

    A steeper view widens the tolerance, and MIN_FACING is the steepest a
    point may be seen and still weigh anything, so this bounds them all; the
    micrometre keeps rounding from ever making the bound the narrower one.
    """
    if not slope_aware:
        return SEEN_TOLERANCE
    steepest = np.sqrt(np.clip(1.0 - MIN_FACING ** 2, 0.0, 1.0)) / MIN_FACING
    per_metre = 1.5 * camera.width / (camera.fx * buffer_width) * steepest
    return SEEN_TOLERANCE + 1e-6 + per_metre * np.maximum(depth, 0.0)


def _seen_tolerance(camera: PhotoCamera, buffer_width: int, depth: np.ndarray, facing: np.ndarray, slope_aware: bool):
    """SEEN_TOLERANCE, widened by the depth change across a buffer pixel and a half when the surface is seen at a slant."""
    if not slope_aware:
        return SEEN_TOLERANCE
    footprint = np.maximum(depth, 0.0) * camera.width / (camera.fx * buffer_width)
    slope = np.sqrt(np.clip(1.0 - facing ** 2, 0.0, 1.0)) / np.maximum(facing, MIN_FACING)
    return SEEN_TOLERANCE + 1.5 * footprint * slope


def _small_static_mask(mask: np.ndarray, height: int, width: int) -> np.ndarray:
    """Block-mean the full-res static mask down to the depth-buffer grid."""
    rows, cols = mask.shape
    row_block = max(1, rows // height)
    col_block = max(1, cols // width)
    trimmed = mask[: row_block * height, : col_block * width]
    blocks = trimmed.reshape(height, row_block, width, col_block)
    return blocks.mean(axis=(1, 3)) >= 0.5


def colour_the_scan(
    vertices: np.ndarray,
    triangles: np.ndarray,
    cameras: list[PhotoCamera],
    images: list[np.ndarray],
    masks: list[np.ndarray] | None = None,
    hidden: Callable[[PhotoCamera], np.ndarray] | None = None,
) -> ColouredScan:
    """Every vertex coloured from the photos that saw it best, evened out for exposure.

    Photos disagree about brightness, so each gets a per-channel gain solved
    from the vertices several of them saw, the same correction the atlas bake
    applies. The top few views are then blended with weights sharpened so the
    best view dominates wherever one clearly wins, and neighbours sourced from
    different photos meet in a soft blend rather than a hard edge.

    ``masks``, when provided, are static-region masks (one per photo,
    full resolution); they are resampled to the depth-buffer grid and samples
    outside the static region are never painted. ``hidden``, when provided,
    names the vertices a camera cannot really see even though nothing scanned
    stands in the way, such as floor under a chair the LiDAR missed.  The resulting scan records
    the best source frame ID per vertex so downstream sampling can prove
    photo support rather than assert it.
    """
    if masks is not None and len(masks) != len(images):
        raise ValueError("masks must have one entry per image")
    normals = vertex_normals(vertices, triangles)
    views = [_ScanView(camera, photo, *_occlusion(camera, vertices, masks, index))
             for index, (camera, photo) in enumerate(zip(cameras, images))]
    hidden_masks = [hidden(view.camera) for view in views] if hidden is not None else None
    gains = _exposure_gains(views, vertices, normals, hidden_masks)
    blend = TopViews(len(vertices))
    best = np.zeros(len(vertices), dtype=np.float32)
    best_view = np.full(len(vertices), -1, dtype=np.int64)
    for index, (view, gain) in enumerate(zip(views, gains)):
        weight, columns, rows = view.weights(vertices, normals)
        if hidden_masks is not None:
            weight = np.where(hidden_masks[index], 0.0, weight)
        seen = np.flatnonzero(weight > 0)
        if not len(seen):
            continue
        colours = np.clip(view.linear(columns[seen], rows[seen]) * gain, 0.0, 1.0)
        blend.add(seen, weight[seen] ** BLEND_SHARPNESS, colours)
        better = seen[weight[seen] > best[seen]]
        best[better] = weight[better]
        best_view[better] = index
    painted = best > 0
    colours = np.tile(UNSEEN, (len(vertices), 1))
    colours[painted] = to_srgb(blend.resolve()[0][painted])
    frame_ids = np.asarray([camera.frame_id for camera in cameras] + [None], dtype=object)
    return ColouredScan(vertices, triangles, np.clip(colours, 0.0, 1.0), painted, sources=frame_ids[best_view])


@dataclass(frozen=True)
class _ScanView:
    camera: PhotoCamera
    photo: np.ndarray
    buffer: np.ndarray
    mask: np.ndarray | None

    def weights(self, vertices: np.ndarray, normals: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return _weights_from(self.camera, vertices, normals, self.buffer, self.mask)

    def linear(self, columns: np.ndarray, rows: np.ndarray) -> np.ndarray:
        return to_linear(bilinear(self.photo, columns, rows).astype(np.float32))


def _occlusion(camera, vertices, masks, index) -> tuple[np.ndarray, np.ndarray | None]:
    buffer = depth_buffer(camera, vertices)
    if masks is None:
        return buffer, None
    return buffer, _small_static_mask(masks[index], *buffer.shape)


def _exposure_gains(
    views: list[_ScanView],
    vertices: np.ndarray,
    normals: np.ndarray,
    hidden_masks: list[np.ndarray] | None = None,
) -> np.ndarray:
    """Per-photo linear gains from an even sample of the vertices several photos saw."""
    picked = np.unique(np.linspace(0, len(vertices) - 1, min(len(vertices), MAX_EXPOSURE_POINTS)).astype(np.int64))
    observations = []
    for index, view in enumerate(views):
        weight, columns, rows = view.weights(vertices[picked], normals[picked])
        if hidden_masks is not None:
            weight = np.where(hidden_masks[index][picked], 0.0, weight)
        seen = np.flatnonzero(weight > 0)
        observations.append((seen, view.linear(columns[seen], rows[seen])))
    return exposure_gains(observations, len(views), len(picked))


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
    mirror_source = None
    if scan.mirror_source is not None:
        kept = scan.mirror_source[used]
        mirror_source = np.where(kept >= 0, remap[np.maximum(kept, 0)], -1)
    return ColouredScan(
        vertices=scan.vertices[used],
        triangles=remap[scan.triangles],
        colours=scan.colours[used],
        seen=scan.seen[used],
        sources=scan.sources[used] if scan.sources is not None else None,
        inferred=scan.inferred[used] if scan.inferred is not None else None,
        sheet_patches=scan.sheet_patches[used] if scan.sheet_patches is not None else None,
        mirror_source=mirror_source,
    )


def write_scan_glb(scan: ColouredScan, out_path: pathlib.Path, max_triangles: int | None = None) -> pathlib.Path:
    """Hand the coloured scan to Blender, which writes the glTF the viewer reads.

    glTF vertex colours are linear, and the viewer encodes them for the screen.
    The scan's colours are the photographs' own sRGB values, so they are made
    linear on the way out; stored as they were, every colour was brightened a
    second time and the whole room looked washed out.
    """
    import tempfile

    from ..blender import _run

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="standardphysics-scan-") as temporary:
        archive = pathlib.Path(temporary) / "scan.npz"
        np.savez(
            archive,
            vertices=scan.vertices.astype(np.float32),
            triangles=scan.triangles.astype(np.int32),
            colours=to_linear(scan.colours).astype(np.float32),
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



def _photo(path: pathlib.Path) -> np.ndarray:
    from PIL import Image

    with Image.open(path) as opened:
        image = opened.convert("RGB")
        image.thumbnail((MAX_PHOTO_EDGE, MAX_PHOTO_EDGE), Image.Resampling.LANCZOS)
        return np.asarray(image, dtype=np.float32) / 255.0


@dataclass(frozen=True)
class DisplayGeometry:
    vertices: np.ndarray
    triangles: np.ndarray
    inferred: np.ndarray
    """Whether each vertex was added rather than scanned."""
    sheet_patches: np.ndarray
    """The added vertices that patch a wall or floor."""
    mirror_source: np.ndarray
    """For a vertex reflected in to complete an object, the vertex it mirrors; -1 otherwise."""


def _people_on(vertices: np.ndarray, graph: SceneGraph, cameras: list[PhotoCamera], people: dict) -> np.ndarray:
    """The vertices that are mostly people.

    Each photo is asked only about the part of the scan inside its frame, and
    its depth buffer is made inside the vote, one photo per core at a time, and
    let go after. A merged floor has thousands of photos: projecting the whole
    floor through each one took minutes, and holding every buffer at once was
    over half a gigabyte.
    """
    from ..discovery.people import mostly_people

    blocks = PointBlocks(vertices)
    views = [(camera, people.get(camera.frame_id, []), None) for camera in cameras]
    return mostly_people(vertices, graph, views, visible_to=blocks.seen_by, depth_buffer_of=depth_buffer)


def _depth_buffers(vertices: np.ndarray, cameras: list[PhotoCamera]) -> list[np.ndarray]:
    """Each camera's depth buffer of the scan, built from only the part of it inside the frame."""
    blocks = PointBlocks(vertices)
    return list(in_parallel(lambda camera: depth_buffer(camera, vertices[blocks.seen_by(camera)]), cameras))


def _display_geometry(
    vertices: np.ndarray, triangles: np.ndarray, graph: SceneGraph,
    cameras: list[PhotoCamera], people: dict | None,
) -> DisplayGeometry:
    """The scan as it should be shown.

    People out, the holes they leave in furniture closed, half-seen furniture
    completed from its other half, and walls and floor made whole, in that order.
    """
    from .hole_patches import with_holes_patched
    from .object_holes import closed_object_holes, without_vertices
    from .symmetry import mirrored_completion, seen_through_by

    voters = evenly_spread(cameras, MAX_GEOMETRY_PHOTOS)
    if people:
        with timed("people removal"):
            vertices, triangles = without_vertices(vertices, triangles, _people_on(vertices, graph, voters, people))
    with timed("object holes"):
        capped = closed_object_holes(vertices, triangles, graph)
    with timed("seen-through buffers"):
        seen_through = seen_through_by(voters, _depth_buffers(capped.vertices, voters))
    with timed("mirrored completion"):
        completed = mirrored_completion(capped.vertices, capped.triangles, graph, seen_through)
    added_so_far = np.concatenate([capped.inferred, completed.added[len(capped.vertices):]])
    with timed("hole patches"):
        patched = with_holes_patched(completed.vertices, completed.triangles, graph)
    extra = len(patched.vertices) - len(completed.vertices)
    return DisplayGeometry(
        vertices=patched.vertices,
        triangles=patched.triangles,
        inferred=np.concatenate([added_so_far, np.ones(extra, dtype=bool)]),
        sheet_patches=patched.sheet_patches,
        mirror_source=np.concatenate([completed.source, np.full(extra, -1, dtype=np.int64)]),
    )


def with_mirrored_colours(scan: ColouredScan) -> ColouredScan:
    """Every reflected vertex coloured like the vertex it mirrors, photographed or filled."""
    if scan.mirror_source is None or not (scan.mirror_source >= 0).any():
        return scan
    colours = scan.colours.copy()
    reflected = np.flatnonzero(scan.mirror_source >= 0)
    colours[reflected] = colours[scan.mirror_source[reflected]]
    return replace(scan, colours=colours)


def coloured_scan(
    mesh_path: pathlib.Path,
    poses_path: pathlib.Path,
    frame_paths: dict[str, pathlib.Path],
    capture_to_room,
    patch_holes_from: SceneGraph | None = None,
    people: dict | None = None,
) -> tuple[ColouredScan, list[PhotoCamera]]:
    """The captured surface in the room frame, each vertex coloured by its best photo.

    With `patch_holes_from`, the scan is first made fit to show: the people in
    `people` (detections per frame id) are taken out, the holes they leave in
    furniture are closed, and the holes in the graph's walls and floor are
    patched, all before colouring, so added surface is coloured by the same
    photos and hidden by the same scanned surfaces as everything else. Photo
    pixels where a person stood never colour anything. Returns the cameras at
    the stored photos' own resolution too, so a caller can go back to a full-size
    photo for a vertex its `sources` names.
    """
    from .hole_patches import hidden_behind_objects
    from .object_holes import people_masks

    all_cameras = [
        camera for camera in load_cameras(poses_path, frame_paths, capture_to_room)
        if frame_paths.get(camera.frame_id, pathlib.Path()).is_file()
    ]
    cameras = evenly_spread(all_cameras, MAX_PHOTOS)
    if not cameras:
        raise ValueError("no stored photo has a usable camera pose")
    vertices, triangles = scan_geometry(mesh_path, capture_to_room)
    inferred, sheet_patches, mirror_source, hidden = None, None, None, None
    if patch_holes_from is not None:
        shown = _display_geometry(vertices, triangles, patch_holes_from, all_cameras, people)
        vertices, triangles = shown.vertices, shown.triangles
        inferred, sheet_patches, mirror_source = shown.inferred, shown.sheet_patches, shown.mirror_source
        hidden = hidden_behind_objects(patch_holes_from, vertices, sheet_patches)
    images = [_photo(frame_paths[camera.frame_id]) for camera in cameras]
    resized = [camera.resized(*image.shape[1::-1]) for camera, image in zip(cameras, images)]
    masks = None
    if people:
        by_frame = people_masks(people, cameras, {c.frame_id: i.shape[:2] for c, i in zip(cameras, images)})
        masks = [by_frame[camera.frame_id] for camera in cameras]
    coloured = colour_the_scan(vertices, triangles, resized, images, masks=masks, hidden=hidden)
    scan = replace(coloured, inferred=inferred, sheet_patches=sheet_patches, mirror_source=mirror_source)
    return unused_vertices_removed(scan), cameras


def shown_scan(
    mesh_path: pathlib.Path,
    poses_path: pathlib.Path,
    frame_paths: dict[str, pathlib.Path],
    graph: SceneGraph,
    people: dict | None = None,
) -> tuple[ColouredScan, list[PhotoCamera]]:
    """The scan made fit to show, with no photograph on it yet, and every camera that took a stored photo.

    People out, holes closed and patched, exactly as `coloured_scan` prepares it,
    without painting its vertices from a sample of the photos: the atlas paints
    from every photo, and its own photographed texels are the better fallback.
    """
    cameras = [
        camera for camera in load_cameras(poses_path, frame_paths, graph.capture_to_room)
        if frame_paths.get(camera.frame_id, pathlib.Path()).is_file()
    ]
    if not cameras:
        raise ValueError("no stored photo has a usable camera pose")
    with timed("mesh load"):
        vertices, triangles = scan_geometry(mesh_path, graph.capture_to_room)
    shown = _display_geometry(vertices, triangles, graph, cameras, people)
    blank = ColouredScan(
        shown.vertices, shown.triangles, np.tile(UNSEEN, (len(shown.vertices), 1)), np.zeros(len(shown.vertices), bool),
        inferred=shown.inferred, sheet_patches=shown.sheet_patches, mirror_source=shown.mirror_source,
    )
    return unused_vertices_removed(blank), cameras


def paint_the_scan(
    mesh_path: pathlib.Path,
    poses_path: pathlib.Path,
    frame_paths: dict[str, pathlib.Path],
    graph: SceneGraph,
    out_path: pathlib.Path,
    materials_dir: pathlib.Path | None = None,
    people: dict | None = None,
) -> ScanPaint:
    """The captured surface, coloured from the photos, as a glTF the viewer can show.

    The surface is thinned, unwrapped and baked from every photo into an atlas,
    which is what makes a shelf of books read as books rather than a smear. A
    texel no photo reached takes the nearest photographed colour, and failing
    that the room's generated material.

    People found by discovery (`people`, detections per frame id) are taken out
    and the holes they leave in furniture closed. Holes the LiDAR left in walls
    and floor are patched with the planes they lie on and coloured from whichever
    photo saw them. Walls, floors and labelled
    objects no photo reached take their generated material, the room's own when
    `materials_dir` holds one, so the room reads whole rather than as photo
    patches on grey and gaps. Everything else stays the
    neutral grey: copying the nearest photographed colour was tried and smeared
    vivid streaks across ceilings and undersides. `painted_fraction` is measured
    before the fill, so it still reports only what a camera saw.
    """
    import time

    from .scan_atlas import bake_scan_atlas
    from .surface_materials import room_materials, unseen_surfaces_filled

    started = time.monotonic()
    scan, every_photo = shown_scan(mesh_path, poses_path, frame_paths, graph, people=people)
    filled = with_mirrored_colours(unseen_surfaces_filled(scan, graph, room_materials(materials_dir)))
    baked = bake_scan_atlas(filled, graph, every_photo, frame_paths, out_path, people=people)
    return ScanPaint(out_path, baked.painted_fraction, baked.photos_used, time.monotonic() - started)
