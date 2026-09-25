"""The painted scan as photographs laid over its surface, rather than one colour per vertex.

A vertex-coloured scan is only as sharp as its vertices are close together, and
once it is thinned for the viewer they sit ten to fifteen centimetres apart, so
a shelf of books reads as a smear. Here the thinned mesh is unwrapped and every
texel, about two centimetres of surface, is coloured from the photographs.

Every photo of the walk is read, not an evenly spaced sixty. More photos mean a
closer, squarer view of each patch of surface and fewer patches nobody saw.
They are read one at a time and let go, twice: once to even out exposure between
them and once to bake, so memory stays flat however long the walk was.

Occlusion is judged against the unwrapped surface itself, sampled texel by
texel, so a surface the thinning moved a few centimetres is not hidden by the
full-resolution scan it no longer quite lies on.

A texel no photo reached takes the colour the vertex painting gave that stretch
of surface, which is itself a photograph or the room's generated material.
"""

from __future__ import annotations

import pathlib
import tempfile
import time
from dataclasses import dataclass

import numpy as np
from PIL import Image
from scipy.sparse import csr_matrix
from scipy.spatial import cKDTree
from standardphysics_contracts import SceneGraph

from .camera import PhotoCamera
from .hole_patches import hidden_behind_objects
from .object_holes import people_masks
from .project import (
    MAX_EXPOSURE_POINTS,
    PointBlocks,
    TopViews,
    bilinear,
    depth_buffer,
    exposure_gains,
    face_normals,
    in_parallel,
    pad_gutters,
    rasterize_atlas,
    to_linear,
    to_srgb,
)
from .scan_colour import BLEND_SHARPNESS, ColouredScan, _small_static_mask, _weights_from
from .stages import timed

TEXEL_METRES = 0.02
MIN_ATLAS_SIZE = 512
MAX_ATLAS_SIZE = 4096
MAX_ATLAS_PHOTOS = 800
"""Photos read per walk. Neighbouring video frames are nearly the same view, so past this they add time and little else."""
PHOTO_EDGE = 1600
EXPOSURE_PHOTO_EDGE = 400
"""Exposure is a per-photo brightness, so it can be read off a thumbnail."""
ATLAS_JPEG_QUALITY = 90
CONSENSUS_VIEWS = 5
"""How many of the best views each texel keeps, so the colour most of them agree on can win."""
FALLBACK_NEIGHBOURS = 8
SPREAD_ROUNDS = 60
GAP_REACH = 0.15
GAP_FACING = 0.7
GAP_NEIGHBOURS = 8
"""How many rings of mesh a photographed colour may spread across to reach corners no photo saw."""
AGREEMENT_DISTANCE = 0.06
"""How close two linear colours are to count as the same surface seen twice."""


@dataclass(frozen=True)
class UnwrappedScan:
    vertices: np.ndarray
    triangles: np.ndarray
    uv: np.ndarray
    """A UV pair for each corner of each triangle, shape (triangles, 3, 2)."""

    @property
    def corners(self) -> np.ndarray:
        return self.vertices[self.triangles]


@dataclass(frozen=True)
class AtlasPaint:
    glb_path: pathlib.Path
    painted_fraction: float
    """The share of texels a photo coloured, before any fill."""
    photos_used: int
    atlas_size: int
    seconds: float


def unwrapped(scan: ColouredScan, work: pathlib.Path, max_triangles: int) -> UnwrappedScan:
    """The scan thinned for the viewer and unwrapped by Blender."""
    from ..blender import _run

    source, result = work / "scan.npz", work / "unwrapped.npz"
    np.savez(source, vertices=scan.vertices.astype(np.float32), triangles=scan.triangles.astype(np.int32))
    output = _run("unwrap_scan.py", ["--scan", str(source), "--out", str(result), "--max-triangles", str(max_triangles)])
    if "SCAN_UNWRAPPED" not in output:
        raise RuntimeError(f"Blender did not unwrap the scan:\n{output[-1500:]}")
    archive = np.load(result)
    return UnwrappedScan(archive["vertices"], archive["triangles"], archive["uv"])


def atlas_size(mesh: UnwrappedScan) -> int:
    """The smallest power-of-two side that gives each texel about TEXEL_METRES of surface."""
    _, world_areas = face_normals(mesh.corners)
    uv = mesh.uv
    first, second = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
    uv_areas = np.abs(first[:, 0] * second[:, 1] - first[:, 1] * second[:, 0]) / 2
    needed = world_areas.sum() / TEXEL_METRES ** 2 / max(uv_areas.sum(), 1e-9)
    side = 2 ** int(np.ceil(np.log2(max(np.sqrt(needed), 1.0))))
    return int(np.clip(side, MIN_ATLAS_SIZE, MAX_ATLAS_SIZE))


def _read(path: pathlib.Path, edge: int) -> np.ndarray:
    with Image.open(path) as opened:
        image = opened.convert("RGB")
        image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        return np.asarray(image, dtype=np.float32) / 255.0


def _pixel_of(uv: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray]:
    """The texel whose centre is nearest a UV point, in the convention `rasterize_atlas` uses."""
    column = np.rint(uv[:, 0] * size - 0.5).astype(np.int64)
    row = np.rint((1.0 - uv[:, 1]) * size - 0.5).astype(np.int64)
    return np.clip(row, 0, size - 1), np.clip(column, 0, size - 1)


def _with_every_face_owned(mesh: UnwrappedScan, size: int, rows, columns, faces, positions, normals):
    """The rasterized texels, plus texels for the faces too small to cover a texel centre of their own.

    Faces are packed one by one at a size in proportion to their area, so the
    smallest cover no texel centre at all, which was two faces in five among the
    specks. A face like that is still drawn, from whatever pixels sit under it:
    the gap fill of some other face's island, a colour from anywhere in the
    room. Each face now also claims the free texels under its centroid and just
    inside its corners, at the matching points on its surface, so every face is
    drawn from pixels baked for it.
    """
    corners, uv = mesh.corners, mesh.uv
    centroid_uv, centroid = uv.mean(axis=1), corners.mean(axis=1)
    points_uv = [centroid_uv] + [0.6 * uv[:, i] + 0.4 * centroid_uv for i in range(3)]
    points = [centroid] + [0.6 * corners[:, i] + 0.4 * centroid for i in range(3)]
    taken = np.zeros(size * size, dtype=bool)
    taken[rows.astype(np.int64) * size + columns] = True
    face_normals_ = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    face_normals_ /= np.maximum(np.linalg.norm(face_normals_, axis=1, keepdims=True), 1e-12)
    extra = [[], [], [], [], []]
    for point_uv, point in zip(points_uv, points):
        row, column = _pixel_of(point_uv, size)
        flat = row * size + column
        _, first = np.unique(flat, return_index=True)
        fresh = np.zeros(len(flat), dtype=bool)
        fresh[first] = True
        fresh &= ~taken[flat]
        taken[flat[fresh]] = True
        for bucket, values in zip(extra, (row, column, np.arange(len(flat)), point, face_normals_)):
            bucket.append(values[fresh])
    return (
        np.concatenate([rows, *extra[0]]).astype(np.int32), np.concatenate([columns, *extra[1]]).astype(np.int32),
        np.concatenate([faces, *extra[2]]).astype(np.int32), np.concatenate([positions, *extra[3]]).astype(np.float32),
        np.concatenate([normals, *extra[4]]).astype(np.float32),
    )


class _Surface:
    """The texels of one atlas and what the photos make of them."""

    def __init__(self, mesh: UnwrappedScan, size: int, painted: ColouredScan, graph: SceneGraph):
        texels = rasterize_atlas(mesh.corners, mesh.uv, np.arange(len(mesh.triangles), dtype=np.int32), size)
        self.size, self.graph, self.mesh = size, graph, mesh
        self.rows, self.columns, self.faces, self.positions, self.normals = _with_every_face_owned(
            mesh, size, texels.rows, texels.columns, texels.owners, texels.positions, texels.normals,
        )
        distances, nearest = cKDTree(painted.vertices).query(self.positions, k=FALLBACK_NEIGHBOURS)
        self.fallback = _blended(to_linear(painted.colours), distances, nearest)
        patches = painted.sheet_patches if painted.sheet_patches is not None else np.zeros(len(painted.vertices), bool)
        self.patch = patches[nearest[:, 0]]
        self.blocks = PointBlocks(self.positions)

    def weights(self, camera: PhotoCamera, photo: np.ndarray, detections: dict, indices: np.ndarray, buffer: np.ndarray):
        """This photo's view weight for each texel in `indices`, and where in the photo to sample it."""
        sized = camera.resized(photo.shape[1], photo.shape[0])
        mask = _people_mask(camera, photo, detections, buffer)
        weight, columns, rows = _weights_from(sized, self.positions[indices], self.normals[indices], buffer, mask, slope_aware=True)
        behind = np.flatnonzero((weight > 0) & self.patch[indices])
        if len(behind):
            hidden = hidden_behind_objects(self.graph, self.positions[indices[behind]], np.ones(len(behind), bool))(camera)
            weight[behind[hidden]] = 0.0
        return weight, columns, rows


def _blended(colours: np.ndarray, distances: np.ndarray, nearest: np.ndarray) -> np.ndarray:
    """Each texel's fallback as the inverse-distance mean of the nearest painted vertices.

    Copying the single nearest vertex tiles a patched floor in five-centimetre
    squares, because a patch's only vertices are the corners of its squares.
    """
    weights = 1.0 / np.maximum(distances, 1e-3)
    return ((weights[:, :, None] * colours[nearest]).sum(axis=1) / weights.sum(axis=1, keepdims=True)).astype(np.float32)


def _people_mask(camera: PhotoCamera, photo: np.ndarray, detections: dict, buffer: np.ndarray) -> np.ndarray | None:
    if not detections.get(camera.frame_id):
        return None
    mask = people_masks(detections, [camera], {camera.frame_id: photo.shape[:2]})[camera.frame_id]
    return _small_static_mask(mask, *buffer.shape)


def _exposure(surface: _Surface, cameras, frame_paths, detections) -> tuple[np.ndarray, list]:
    """Per-photo gains from an even sample of texels, and each photo's depth buffer for the bake."""
    sample = np.unique(np.linspace(0, len(surface.positions) - 1, MAX_EXPOSURE_POINTS).astype(np.int64))
    in_sample = np.full(len(surface.positions), -1, dtype=np.int64)
    in_sample[sample] = np.arange(len(sample))

    nothing = (np.empty(0, np.int64), np.empty((0, 3), np.float32))

    def measure(camera):
        visible = surface.blocks.seen_by(camera)
        if not len(visible):
            return False, nothing
        sampled = visible[in_sample[visible] >= 0]
        if not len(sampled):
            return True, nothing
        buffer = depth_buffer(camera, surface.positions[visible])
        photo = _read(frame_paths[camera.frame_id], EXPOSURE_PHOTO_EDGE)
        weight, columns, rows = surface.weights(camera, photo, detections, sampled, buffer)
        seen = np.flatnonzero(weight > 0)
        return True, (in_sample[sampled[seen]], to_linear(bilinear(photo, columns[seen], rows[seen])))

    measured = list(in_parallel(measure, cameras))
    framed = [sees for sees, _ in measured]
    return exposure_gains([seen for _, seen in measured], len(cameras), len(sample)), framed


def _bake(surface: _Surface, cameras, frame_paths, detections, gains, framed) -> tuple[np.ndarray, np.ndarray]:
    """Linear colour per texel and whether any photo reached it.

    Each photo's depth buffer is built again here rather than kept from the
    exposure pass: thousands of them held at once were more memory than a
    small server has, and one is quick to make from the texels in its frame.
    The best views keep their colours at half precision, which is still eight
    times finer than the atlas stores and halves the largest array of the bake.
    """
    blend = TopViews(len(surface.positions), CONSENSUS_VIEWS, color_type=np.float16)
    painted = np.zeros(len(surface.positions), dtype=bool)

    def sample(job):
        camera, gain = job
        visible = surface.blocks.seen_by(camera)
        buffer = depth_buffer(camera, surface.positions[visible])
        photo = _read(frame_paths[camera.frame_id], PHOTO_EDGE)
        weight, columns, rows = surface.weights(camera, photo, detections, visible, buffer)
        seen = np.flatnonzero(weight > 0)
        colours = np.clip(to_linear(bilinear(photo, columns[seen], rows[seen])) * gain, 0.0, 1.0)
        return visible[seen], weight[seen], colours

    jobs = [(camera, gain) for camera, gain, sees in zip(cameras, gains, framed) if sees]
    for indices, weights, colours in in_parallel(sample, jobs):
        blend.add(indices, weights, colours)
        painted[indices] = True
    return _in_chunks(agreed_colours, blend.weights, blend.colors), painted


def _in_chunks(resolve, weights: np.ndarray, colours: np.ndarray, size: int = 250_000) -> np.ndarray:
    """The resolution run a slice at a time, since comparing every pair of views is five-by-five per texel."""
    return np.concatenate([resolve(weights[start:start + size], colours[start:start + size].astype(np.float32))
                           for start in range(0, len(weights), size)] or [np.empty((0, 3), np.float32)])


def agreed_colours(weights: np.ndarray, colours: np.ndarray) -> np.ndarray:
    """The colour the most view weight agrees on, per texel, blended from the views that agree.

    People walk through a capture and the LiDAR rarely keeps them, so the photo
    with the best view of a stretch of floor can be the one with somebody
    standing on it. Taking that view because it is best paints them on the
    floor. Each kept view instead counts the weight of the views whose colour
    matches its own, and the most supported colour wins: a person one photo saw
    loses to the floor four photos saw. Within the winning group the best view
    still dominates, so detail comes from the sharpest photo that agrees.
    """
    distance = np.linalg.norm(colours[:, :, None, :] - colours[:, None, :, :], axis=3)
    support = ((distance <= AGREEMENT_DISTANCE) * weights[:, None, :]).sum(axis=2)
    winner = np.argmax(np.where(weights > 0, support, -1.0), axis=1)
    agreeing = (distance[np.arange(len(weights)), winner] <= AGREEMENT_DISTANCE) & (weights > 0)
    sharpened = np.where(agreeing, weights, 0.0) ** BLEND_SHARPNESS
    total = sharpened.sum(axis=1, keepdims=True)
    return ((sharpened[:, :, None] * colours).sum(axis=1) / np.maximum(total, 1e-12)).astype(np.float32)


def _face_filled(surface: _Surface, colours: np.ndarray, painted: np.ndarray) -> np.ndarray:
    """Each texel's colour, with every unpainted texel taking the photographed colour of the surface around it.

    A texel no photo reached blends the colours of its face's three corners,
    and a corner's colour is the photographed faces around it, spread along the
    mesh to corners with none. That keeps a fill on the surface it belongs to:
    the nearest photographed texel in space was as often the edge of a chair
    or the far side of a gap, and every face filled that way was a speck of
    somebody else's colour. Only where no photograph reaches along the surface
    does the room's material stand.
    """
    field, known = _corner_colours(surface.mesh, surface.faces, colours, painted)
    corners = surface.mesh.triangles[surface.faces]
    reached = known[corners].all(axis=1)
    weights = _barycentric(surface.mesh.vertices[corners], surface.positions)
    blended = np.einsum("ij,ijk->ik", weights, field[corners]).astype(np.float32)
    fallback = np.where(reached[:, None], blended, surface.fallback)
    return np.where(painted[:, None], colours, fallback)


def _corner_colours(mesh: UnwrappedScan, faces: np.ndarray, colours: np.ndarray, painted: np.ndarray):
    """A colour per mesh vertex from the photographed faces around it, spread along the mesh to the rest."""
    face_count = len(mesh.triangles)
    count = np.bincount(faces[painted], minlength=face_count).astype(np.float64)
    sums = np.stack([np.bincount(faces[painted], weights=colours[painted, channel], minlength=face_count) for channel in range(3)], axis=1)
    face_known = count > 0
    face_mean = sums / np.maximum(count, 1)[:, None]
    incidence = csr_matrix(
        (np.ones(mesh.triangles.size), (mesh.triangles.ravel(), np.repeat(np.arange(face_count), 3))),
        shape=(len(mesh.vertices), face_count),
    )
    weight = incidence @ face_known.astype(np.float64)
    field = (incidence @ (face_mean * face_known[:, None])) / np.maximum(weight, 1e-12)[:, None]
    field, known = _spread(incidence @ incidence.T, field, weight > 0)
    return _borrowed_across_gaps(mesh, incidence, field, known)


def _borrowed_across_gaps(mesh: UnwrappedScan, incidence, field: np.ndarray, known: np.ndarray):
    """Vertices on a fragment no photograph reaches along the mesh take a close vertex facing the same way.

    A phone's LiDAR arrives in overlapping pieces that share no vertices, so a
    small piece can sit in the middle of a photographed floor with no mesh path
    to it. Spreading along the mesh never reaches it, and it showed as specks
    of unmeasured grey. The nearest known vertex within GAP_REACH that faces
    within GAP_FACING of the same way is almost always the same surface; a
    vertex with none keeps the room's material.
    """
    if known.all() or not known.any():
        return field, known
    corners = mesh.vertices[mesh.triangles]
    face_normals_ = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normals = incidence @ face_normals_
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    lost, found = np.flatnonzero(~known), np.flatnonzero(known)
    distance, nearest = cKDTree(mesh.vertices[found]).query(mesh.vertices[lost], k=GAP_NEIGHBOURS, distance_upper_bound=GAP_REACH)
    candidates = found[np.minimum(nearest, len(found) - 1)]
    agree = np.isfinite(distance) & (np.einsum("ij,ikj->ik", normals[lost], normals[candidates]) > GAP_FACING)
    has = agree.any(axis=1)
    pick = candidates[np.arange(len(lost)), np.argmax(agree, axis=1)]
    field[lost[has]] = field[pick[has]]
    known = known.copy()
    known[lost[has]] = True
    return field, known


def _spread(adjacency, field: np.ndarray, known: np.ndarray, rounds: int = SPREAD_ROUNDS):
    """Unknown vertices take the mean of their known neighbours, one ring further out each round."""
    for _ in range(rounds):
        reach = adjacency @ known.astype(np.float64)
        sums = adjacency @ (field * known[:, None])
        newly = ~known & (reach > 0)
        if not newly.any():
            break
        field[newly] = sums[newly] / reach[newly][:, None]
        known = known | newly
    return field, known


def _barycentric(corners: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Each point's weights over its triangle's three corners, clipped into the triangle."""
    first, second, offset = corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0], points - corners[:, 0]
    d00, d01, d11 = np.einsum("ij,ij->i", first, first), np.einsum("ij,ij->i", first, second), np.einsum("ij,ij->i", second, second)
    d20, d21 = np.einsum("ij,ij->i", offset, first), np.einsum("ij,ij->i", offset, second)
    denominator = np.maximum(d00 * d11 - d01 * d01, 1e-18)
    v, w = (d11 * d20 - d01 * d21) / denominator, (d00 * d21 - d01 * d20) / denominator
    weights = np.clip(np.stack([1 - v - w, v, w], axis=1), 0.0, 1.0)
    return weights / np.maximum(weights.sum(axis=1, keepdims=True), 1e-12)


def _atlas_image(surface: _Surface, colours: np.ndarray, painted: np.ndarray) -> Image.Image:
    linear = _face_filled(surface, colours, painted)
    image = np.zeros((surface.size, surface.size, 3), dtype=np.float32)
    image[surface.rows, surface.columns] = linear
    filled = np.zeros((surface.size, surface.size), dtype=bool)
    filled[surface.rows, surface.columns] = True
    return Image.fromarray(np.rint(to_srgb(pad_gutters(image, filled)) * 255).astype(np.uint8), "RGB")


def _write_glb(mesh_path: pathlib.Path, atlas_path: pathlib.Path, out_path: pathlib.Path) -> None:
    from ..blender import _run

    output = _run("texture_scan.py", ["--mesh", str(mesh_path), "--atlas", str(atlas_path), "--out", str(out_path)])
    if "SCAN_GLB_WRITTEN" not in output:
        raise RuntimeError(f"Blender did not write the textured scan:\n{output[-1500:]}")


def _evenly_spread(cameras: list[PhotoCamera], limit: int) -> list[PhotoCamera]:
    if len(cameras) <= limit:
        return cameras
    picks = np.linspace(0, len(cameras) - 1, limit).round().astype(int)
    return [cameras[index] for index in dict.fromkeys(picks.tolist())]


def bake_scan_atlas(
    painted: ColouredScan,
    graph: SceneGraph,
    cameras: list[PhotoCamera],
    frame_paths: dict[str, pathlib.Path],
    out_path: pathlib.Path,
    people: dict | None = None,
    max_triangles: int = 260_000,
) -> AtlasPaint:
    """The vertex-painted scan, thinned, unwrapped and baked from every photo into one textured glTF."""
    started = time.monotonic()
    chosen = _evenly_spread(cameras, MAX_ATLAS_PHOTOS)
    detections = people or {}
    with tempfile.TemporaryDirectory(prefix="standardphysics-atlas-") as temporary:
        work = pathlib.Path(temporary)
        with timed("unwrap"):
            mesh = unwrapped(painted, work, max_triangles)
        size = atlas_size(mesh)
        with timed("texels"):
            surface = _Surface(mesh, size, painted, graph)
        with timed("exposure"):
            gains, framed = _exposure(surface, chosen, frame_paths, detections)
        with timed("photo bake"):
            colours, reached = _bake(surface, chosen, frame_paths, detections, gains, framed)
        atlas_path, mesh_path = work / "atlas.jpg", work / "mesh.npz"
        with timed("atlas image"):
            _atlas_image(surface, colours, reached).save(atlas_path, quality=ATLAS_JPEG_QUALITY)
        np.savez(mesh_path, vertices=mesh.vertices, triangles=mesh.triangles, uv=mesh.uv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _write_glb(mesh_path, atlas_path, out_path)
    return AtlasPaint(out_path, float(reached.mean()) if len(reached) else 0.0, len(chosen), size, time.monotonic() - started)
