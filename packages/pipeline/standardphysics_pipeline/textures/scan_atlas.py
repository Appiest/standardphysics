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

import ctypes
import pathlib
import tempfile
import time
from dataclasses import dataclass

import fast_simplification
import numpy as np
from PIL import Image
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from standardphysics_contracts import SceneGraph

from .camera import PhotoCamera
from .hole_patches import hidden_behind_objects
from .object_holes import people_masks
from .project import (
    MAX_EXPOSURE_POINTS,
    DepthPyramid,
    PointBlocks,
    TopViews,
    TriangleBlocks,
    bilinear,
    evenly_spread,
    exposure_gains,
    face_normals,
    in_parallel,
    occluder_depth_buffer,
    pad_gutters,
    rasterize_atlas,
    sphere_footprints,
    to_linear,
    to_srgb,
)
from .scan_colour import BLEND_SHARPNESS, ColouredScan, PickedRows, _small_static_mask, _weights_from, _widest_tolerance
from .stages import timed

TEXEL_METRES = 0.02
MIN_ATLAS_SIZE = 512
MAX_ATLAS_SIZE = 4096
MAX_ATLAS_PHOTOS = 800
"""Photos read per walk. Neighbouring video frames are nearly the same view, so past this they add time and little else."""
PHOTO_EDGE = 2048
"""Photos up to this size are sampled as the camera stored them: a phone's 1920 frames are sharper whole, and shrinking them cost more than the rest of a photo's work."""
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
    atlases: np.ndarray | None = None
    """Which atlas each triangle's UVs are packed into; all in one when absent."""

    @property
    def corners(self) -> np.ndarray:
        return self.vertices[self.triangles]

    @property
    def atlas_count(self) -> int:
        return int(self.atlases.max()) + 1 if self.atlases is not None and len(self.atlases) else 1

    def atlas(self, index: int) -> UnwrappedScan:
        """The triangles packed into one atlas, over the same vertices."""
        if self.atlases is None:
            return self
        chosen = self.atlases == index
        return UnwrappedScan(self.vertices, self.triangles[chosen], self.uv[chosen], self.atlases[chosen])


@dataclass(frozen=True)
class _Atlas:
    path: pathlib.Path
    size: int
    texels: int
    reached: int


@dataclass(frozen=True)
class AtlasPaint:
    glb_path: pathlib.Path
    painted_fraction: float
    """The share of texels a photo coloured, before any fill."""
    photos_used: int
    atlas_size: int
    seconds: float


WELD_METRES = 0.001
"""Vertices this close are one vertex: the shared corners of patch squares, and the seams between LiDAR anchors."""
BLENDER_FACE_FACTOR = 6
"""How many times the viewer's face count Blender is handed to thin the rest of the way.

Blender's thinning keeps more of the surface than the quadric pass does, so it
is left a share of the work: at four times, a scanned room kept 96.5 per cent
of its area against 97.1 at six. Blender needs about 0.6 GB per million faces
over a 0.7 GB base, so six times the viewer's 260,000 faces is under two
gigabytes, where a library floor's four million had needed three."""
MAX_BLENDER_FACES = 1_560_000
"""The most faces Blender is ever handed, which holds it under two gigabytes however large the viewer's budget."""
VIEWER_SHARE = 4
"""The viewer keeps about one face in this many of the scan, the thinning a single walk has always had."""
MIN_VIEWER_FACES = 260_000
MAX_VIEWER_FACES = 1_000_000
"""The most faces the viewer is given, which is what four walks of a library floor came to when each was painted alone."""
ATLAS_FACES = MIN_VIEWER_FACES
"""The faces one atlas holds, so every face gets the texels a single room's faces get."""


def atlas_count(faces: int) -> int:
    """How many atlases the viewer's faces are packed into, one per ATLAS_FACES."""
    return max(1, -(-faces // ATLAS_FACES))


def viewer_faces(scan: ColouredScan) -> int:
    """How many faces the painted scan keeps, in proportion to how much was scanned.

    One budget for every capture thinned a whole library floor as hard as one
    room: four million faces cut to 260,000 broke the floor into shards with
    gaps between them. A floor now keeps as many faces as its walks had when
    each was painted alone.
    """
    return int(np.clip(len(scan.triangles) // VIEWER_SHARE, MIN_VIEWER_FACES, MAX_VIEWER_FACES))


def unwrapped(scan: ColouredScan, work: pathlib.Path, max_triangles: int, atlases: int = 1) -> UnwrappedScan:
    """The scan thinned for the viewer and unwrapped by Blender.

    Blender used to be handed the whole scan. A library floor of four million
    faces ran it out of memory on a two-core server with four gigabytes, and the
    painted scan was lost. It now receives a mesh already thinned to a few times
    the viewer's size, so what it holds is bounded however large the capture.
    """
    from ..blender import _run

    handed = min(BLENDER_FACE_FACTOR * max_triangles, MAX_BLENDER_FACES)
    vertices, triangles = thinned_for_blender(scan.vertices, scan.triangles, handed)
    source, result = work / "scan.npz", work / "unwrapped.npz"
    np.savez(source, vertices=vertices.astype(np.float32), triangles=triangles.astype(np.int32))
    output = _run("unwrap_scan.py", [
        "--scan", str(source), "--out", str(result), "--max-triangles", str(max_triangles), "--atlases", str(atlases),
    ])
    if "SCAN_UNWRAPPED" not in output:
        raise RuntimeError(f"Blender did not unwrap the scan:\n{output[-1500:]}")
    archive = np.load(result)
    return UnwrappedScan(archive["vertices"], archive["triangles"], archive["uv"], archive["atlases"])


def thinned_for_blender(vertices: np.ndarray, triangles: np.ndarray, max_faces: int) -> tuple[np.ndarray, np.ndarray]:
    """The mesh welded into one surface and, when larger than `max_faces`, thinned toward it with its open edges held.

    Welding comes first: hole patches arrive as separate squares and the LiDAR
    as separate anchors, and thinned apart each piece collapses on its own, so a
    patched floor came out as a lattice with gaps.

    Quadric thinning here keeps the surface within a centimetre, but it drags
    the rim of every hole inward, and about one vertex in seven of a scanned
    room lies on a rim. So it thins only the interior and leaves the rims to
    Blender's decimation, which keeps them in place.
    """
    points, faces = welded(vertices, triangles)
    if len(faces) <= max_faces:
        return points, faces
    return fast_simplification.simplify(
        points.astype(np.float64), faces.astype(np.int64), target_count=max_faces, preserve_border=True,
    )


def welded(vertices: np.ndarray, triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vertices within WELD_METRES of each other joined, and the faces this collapses or repeats dropped."""
    pairs = cKDTree(vertices).query_pairs(WELD_METRES, output_type="ndarray")
    if len(pairs):
        links = csr_matrix((np.ones(len(pairs), dtype=bool), (pairs[:, 0], pairs[:, 1])), shape=(len(vertices),) * 2)
        _, group = connected_components(links, directed=False)
    else:
        group = np.arange(len(vertices))
    first = np.full(group.max() + 1, len(vertices), dtype=np.int64)
    np.minimum.at(first, group, np.arange(len(vertices)))
    faces = group[triangles]
    faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])]
    _, unique = np.unique(np.sort(faces, axis=1), axis=0, return_index=True)
    faces = faces[np.sort(unique)]
    used, compact = np.unique(faces, return_inverse=True)
    return vertices[first[used]], compact.reshape(-1, 3)


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
    """The photo as linear-ready floats, no larger than `edge`; a JPEG is decoded straight at the nearest smaller scale."""
    with Image.open(path) as opened:
        opened.draft("RGB", (edge, edge))
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
        self.normals = self.normals.astype(np.float16)
        self.corner_weights = _corner_weights(mesh, self.faces, self.positions)
        self.fallback, self.patch = _fallback_and_patch(painted, mesh, self.faces, self.corner_weights)
        self.blocks = PointBlocks(self.positions)

    def visible_to(self, camera: PhotoCamera, buffer: np.ndarray) -> np.ndarray:
        return unhidden_members(self.blocks, camera, buffer)

    def weights(self, camera: PhotoCamera, photo: np.ndarray, detections: dict, indices: np.ndarray, buffer: np.ndarray):
        """This photo's view weight for each texel in `indices`, and where in the photo to sample it."""
        sized = camera.resized(photo.shape[1], photo.shape[0])
        mask = _people_mask(camera, photo, detections, buffer)
        weight, columns, rows = _weights_from(sized, self.positions[indices], PickedRows(self.normals, indices), buffer, mask, slope_aware=True)
        positive = np.flatnonzero(weight > 0)
        behind = positive[self.patch[indices[positive]]]
        if len(behind):
            hidden = hidden_behind_objects(self.graph, self.positions[indices[behind]], np.ones(len(behind), bool))(camera)
            weight[behind[hidden]] = 0.0
        return weight, columns, rows


TEXEL_CHUNK = 1_000_000
"""Texels worked on at once where each needs its face's three corners, so the gathered corners stay near a hundred megabytes."""


def _corner_weights(mesh: UnwrappedScan, faces: np.ndarray, positions: np.ndarray) -> np.ndarray:
    """Each texel's barycentric weights over its face's corners, kept for the fallback and the fill."""
    weights = np.empty((len(positions), 3), dtype=np.float32)
    for start in range(0, len(positions), TEXEL_CHUNK):
        corners = mesh.vertices[mesh.triangles[faces[start:start + TEXEL_CHUNK]]]
        weights[start:start + TEXEL_CHUNK] = _barycentric(corners, positions[start:start + TEXEL_CHUNK])
    return weights


def _fallback_and_patch(painted: ColouredScan, mesh: UnwrappedScan, faces: np.ndarray, weights: np.ndarray):
    """Each texel's fallback colour, and whether it lies on a wall or floor patch, from its face's corners.

    The fallback is the inverse-distance blend of the nearest painted vertices,
    worked out at each corner of the atlas's faces and blended across the face.
    Asking for the neighbours of every texel instead was sixteen million
    lookups an atlas, over half its texel step on the droplet.
    """
    corners = mesh.triangles[faces]
    used = np.unique(corners)
    distances, nearest = cKDTree(painted.vertices).query(mesh.vertices[used], k=FALLBACK_NEIGHBOURS, workers=-1)
    at_corner = np.zeros((len(mesh.vertices), 3), dtype=np.float32)
    at_corner[used] = _blended(to_linear(painted.colours), distances, nearest)
    patches = painted.sheet_patches if painted.sheet_patches is not None else np.zeros(len(painted.vertices), bool)
    on_patch = np.zeros(len(mesh.vertices), dtype=bool)
    on_patch[used] = patches[nearest[:, 0]]
    fallback = np.empty((len(faces), 3), dtype=np.float16)
    for start in range(0, len(faces), TEXEL_CHUNK):
        chunk = slice(start, start + TEXEL_CHUNK)
        fallback[chunk] = np.einsum("ij,ijk->ik", weights[chunk], at_corner[corners[chunk]])
    patch = on_patch[corners[np.arange(len(faces)), weights.argmax(axis=1)]]
    return fallback, patch


def unhidden_members(blocks: PointBlocks, camera: PhotoCamera, buffer: np.ndarray) -> np.ndarray:
    """Indices of the points in cubes that reach the frame and are not wholly behind the depth buffer.

    A photo across a library floor frames a million texels and paints about one
    in a hundred; most sit behind shelves and walls. A cube whose nearest point
    lies beyond the farthest recorded depth over its footprint, by more than the
    widest tolerance any of its points could get, holds only points the depth
    test would reject one by one.
    """
    cubes = blocks.cubes_seen_by(camera)
    radii = blocks.radii(cubes)
    footprints = sphere_footprints(camera.resized(buffer.shape[1], buffer.shape[0]), blocks.centres[cubes], radii, margin=1)
    farthest = DepthPyramid(buffer).farthest(footprints.left, footprints.right, footprints.top, footprints.bottom)
    tolerance = _widest_tolerance(camera, buffer.shape[1], footprints.nearest + 2 * radii, slope_aware=True)
    hidden = footprints.usable & (footprints.nearest > farthest + tolerance)
    return blocks.members(cubes[~hidden])


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


class _Occluders:
    """Each photo's depth buffer of the whole thinned surface, drawn once and read by every atlas.

    An atlas holds one stretch of a floor. A buffer drawn from its own texels
    missed a shelf standing in the next stretch, so the floor behind it could
    take the shelf's colour, and the same buffers were drawn again for every
    atlas and for exposure. Faces also cover the gaps between texels.
    """

    def __init__(self, mesh: UnwrappedScan, cameras: list[PhotoCamera]):
        corners = mesh.corners.astype(np.float32)
        blocks = TriangleBlocks(corners)
        buffers = in_parallel(lambda camera: occluder_depth_buffer(camera, corners, blocks), cameras)
        self.buffers = {camera.frame_id: buffer for camera, buffer in zip(cameras, buffers)}

    def of(self, camera: PhotoCamera) -> np.ndarray:
        return self.buffers[camera.frame_id]


def _exposure(surface: _Surface, cameras, frame_paths, detections, occluders: _Occluders) -> tuple[np.ndarray, list]:
    """Per-photo gains from an even sample of texels, and each photo's depth buffer for the bake."""
    sample = np.unique(np.linspace(0, len(surface.positions) - 1, MAX_EXPOSURE_POINTS).astype(np.int64))
    in_sample = np.full(len(surface.positions), -1, dtype=np.int64)
    in_sample[sample] = np.arange(len(sample))

    nothing = (np.empty(0, np.int64), np.empty((0, 3), np.float32))

    def measure(camera):
        buffer = occluders.of(camera)
        visible = surface.visible_to(camera, buffer)
        if not len(visible):
            return False, nothing
        sampled = visible[in_sample[visible] >= 0]
        if not len(sampled):
            return True, nothing
        photo = _read(frame_paths[camera.frame_id], EXPOSURE_PHOTO_EDGE)
        weight, columns, rows = surface.weights(camera, photo, detections, sampled, buffer)
        seen = np.flatnonzero(weight > 0)
        return True, (in_sample[sampled[seen]], to_linear(bilinear(photo, columns[seen], rows[seen])))

    measured = list(in_parallel(measure, cameras))
    framed = [sees for sees, _ in measured]
    return exposure_gains([seen for _, seen in measured], len(cameras), len(sample)), framed


def _bake(surface: _Surface, cameras, frame_paths, detections, gains, framed, occluders: _Occluders) -> tuple[np.ndarray, np.ndarray]:
    """Linear colour per texel and whether any photo reached it.

    The best views keep their colours at half precision, which is still eight
    times finer than the atlas stores and halves the largest array of the bake.
    """
    blend = TopViews(len(surface.positions), CONSENSUS_VIEWS, color_type=np.float16)
    painted = np.zeros(len(surface.positions), dtype=bool)

    def sample(job):
        camera, gain = job
        buffer = occluders.of(camera)
        visible = surface.visible_to(camera, buffer)
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
    """The resolution run a slice at a time on every core, since comparing every pair of views is five-by-five per texel."""
    def slice_of(start: int) -> np.ndarray:
        return resolve(weights[start:start + size].astype(np.float32), colours[start:start + size].astype(np.float32))

    return np.concatenate(list(in_parallel(slice_of, range(0, len(weights), size), counted=False)) or [np.empty((0, 3), np.float32)])


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
    blended = np.einsum("ij,ijk->ik", surface.corner_weights, field[corners]).astype(np.float32)
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


def _write_glb(mesh_path: pathlib.Path, atlas_paths: list[pathlib.Path], out_path: pathlib.Path) -> None:
    from ..blender import _run

    atlases = [argument for path in atlas_paths for argument in ("--atlas", str(path))]
    output = _run("texture_scan.py", ["--mesh", str(mesh_path), *atlases, "--out", str(out_path)])
    if "SCAN_GLB_WRITTEN" not in output:
        raise RuntimeError(f"Blender did not write the textured scan:\n{output[-1500:]}")


def _return_freed_memory() -> None:
    """Give the pages an atlas freed back to the system before the next atlas starts.

    The allocator keeps freed memory for reuse, and on the droplet each atlas
    began a little higher than the last until the fourth was killed at 3.1 GB.
    Only glibc has the call; elsewhere the memory is left where it is.
    """
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def _baked_atlas(mesh: UnwrappedScan, painted: ColouredScan, graph: SceneGraph, cameras, frame_paths, detections, path, occluders) -> _Atlas:
    """One atlas's faces baked from the photos into an image at `path`."""
    size = atlas_size(mesh)
    with timed("texels"):
        surface = _Surface(mesh, size, painted, graph)
    with timed("exposure"):
        gains, framed = _exposure(surface, cameras, frame_paths, detections, occluders)
    with timed("photo bake"):
        colours, reached = _bake(surface, cameras, frame_paths, detections, gains, framed, occluders)
    with timed("atlas image"):
        _atlas_image(surface, colours, reached).save(path, quality=ATLAS_JPEG_QUALITY)
    return _Atlas(path, size, len(reached), int(reached.sum()))


def bake_scan_atlas(
    painted: ColouredScan,
    graph: SceneGraph,
    cameras: list[PhotoCamera],
    frame_paths: dict[str, pathlib.Path],
    out_path: pathlib.Path,
    people: dict | None = None,
    max_triangles: int | None = None,
) -> AtlasPaint:
    """The vertex-painted scan, thinned, unwrapped and baked from every photo into one textured glTF.

    `max_triangles` is the viewer's face budget, `viewer_faces` of the scan unless given.
    A budget larger than one atlas holds is packed into several, baked one after
    another so only one atlas's texels are in memory at a time.
    """
    started = time.monotonic()
    chosen = evenly_spread(cameras, MAX_ATLAS_PHOTOS)
    detections = people or {}
    budget = max_triangles or viewer_faces(painted)
    with tempfile.TemporaryDirectory(prefix="standardphysics-atlas-") as temporary:
        work = pathlib.Path(temporary)
        with timed("unwrap"):
            mesh = unwrapped(painted, work, budget, atlas_count(budget))
        with timed("occlusion"):
            occluders = _Occluders(mesh, chosen)
        atlases = []
        for index in range(mesh.atlas_count):
            atlases.append(_baked_atlas(
                mesh.atlas(index), painted, graph, chosen, frame_paths, detections, work / f"atlas-{index}.jpg", occluders,
            ))
            _return_freed_memory()
        mesh_path = work / "mesh.npz"
        np.savez(mesh_path, vertices=mesh.vertices, triangles=mesh.triangles, uv=mesh.uv, atlases=mesh.atlases)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _write_glb(mesh_path, [atlas.path for atlas in atlases], out_path)
    texels = sum(atlas.texels for atlas in atlases)
    painted_fraction = sum(atlas.reached for atlas in atlases) / texels if texels else 0.0
    size = max(atlas.size for atlas in atlases)
    return AtlasPaint(out_path, painted_fraction, len(chosen), size, time.monotonic() - started)
