"""The texels of an atlas: which surface point and face every texel centre inside a triangle lands on."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

GUTTER_PASSES = 8


@dataclass(frozen=True)
class Texels:
    rows: np.ndarray
    columns: np.ndarray
    positions: np.ndarray
    normals: np.ndarray
    owners: np.ndarray
    base_colours: np.ndarray

    def __len__(self) -> int:
        return len(self.rows)


def face_normals(world: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Unit normals and areas for (T, 3, 3) triangles."""
    cross = np.cross(world[:, 1] - world[:, 0], world[:, 2] - world[:, 0])
    length = np.linalg.norm(cross, axis=1)
    return cross / np.maximum(length, 1e-12)[:, None], length / 2


ATLAS_BATCH_SIDES = (4, 8, 12, 24, 48)
"""The candidate squares faces are sorted into and rasterized together in; faces wider than the last are drawn one at a time."""
ATLAS_BATCH_CANDIDATES = 1_500_000
"""Candidate texels tested in one step, in doubles about a hundred megabytes."""


def rasterize_atlas(
    world: np.ndarray, uv: np.ndarray, owners: np.ndarray, size: int,
    base_colours: np.ndarray | None = None,
) -> Texels:
    """Every texel centre inside a triangle, with the surface point and owner it lands on.

    UV v runs up and image rows run down, so row = (1 - v) * size, with texel
    centres at integer coordinates. Where faces overlap, the later face owns the texel.

    Packed faces are a few texels across, and drawing them one by one was most
    of a floor's texel step: a Python call per face, a quarter of a million per
    atlas. Small faces are drawn together with the same arithmetic.
    """
    normals, areas = face_normals(world)
    if base_colours is None:
        base_colours = np.full((len(world), 3), 0.65, dtype=np.float32)
    x = uv[:, :, 0] * size - 0.5
    y = (1.0 - uv[:, :, 1]) * size - 0.5
    first_column, first_row = np.maximum(0, np.floor(x.min(axis=1))), np.maximum(0, np.floor(y.min(axis=1)))
    width = np.minimum(size - 1, np.ceil(x.max(axis=1))) - first_column + 1
    height = np.minimum(size - 1, np.ceil(y.max(axis=1))) - first_row + 1
    drawn = (areas > 1e-10) & (width > 0) & (height > 0)
    side = np.maximum(width, height)
    pieces = [_small_texels(x, y, first_column, first_row, width, height, faces, size, square)
              for faces, square in _batches(np.flatnonzero(drawn), side)]
    pieces += [_large_texels(world, uv, index, size) for index in np.flatnonzero(drawn & (side > ATLAS_BATCH_SIDES[-1]))]
    pieces = [_placed(world, *piece) for piece in pieces if piece is not None and len(piece[0])]
    if not pieces:
        empty = np.empty((0, 3), dtype=np.float32)
        return Texels(np.empty(0, np.int32), np.empty(0, np.int32), empty, empty, np.empty(0, np.int32), empty)
    faces, rows, columns, positions = (np.concatenate(parts) for parts in zip(*pieces))
    keep = _last_face_per_texel(faces, rows.astype(np.int64) * size + columns)
    faces = faces[keep]
    return Texels(
        rows[keep], columns[keep], positions[keep],
        normals[faces].astype(np.float32), owners[faces].astype(np.int32), np.asarray(base_colours, dtype=np.float32)[faces],
    )


def _placed(world, faces, rows, columns, weights):
    """A batch's texels with their surface points, kept compact: the weights are dropped once the points are made."""
    corners = world[faces]
    positions = (weights[:, 0, None] * corners[:, 0] + weights[:, 1, None] * corners[:, 1] + weights[:, 2, None] * corners[:, 2]).astype(np.float32)
    return faces.astype(np.int32), rows.astype(np.int32), columns.astype(np.int32), positions


def _last_face_per_texel(faces: np.ndarray, keys: np.ndarray) -> np.ndarray:
    """For each texel, sorted by texel, the entry of the latest face that covers it."""
    order = np.lexsort((faces, keys))
    last = np.append(keys[order][1:] != keys[order][:-1], True)
    return order[last]


def _batches(faces: np.ndarray, side: np.ndarray):
    """The faces in groups of one candidate square each, a square's worth of candidates at a time."""
    smaller = 0
    for square in ATLAS_BATCH_SIDES:
        chosen = faces[(side[faces] > smaller) & (side[faces] <= square)]
        smaller = square
        batch = max(1, ATLAS_BATCH_CANDIDATES // square ** 2)
        for start in range(0, len(chosen), batch):
            yield chosen[start:start + batch], square


def _small_texels(x, y, first_column, first_row, width, height, faces, size, square):
    """Texel centres inside many small faces at once, as (faces, rows, columns, barycentric weights)."""
    offsets = np.arange(square)
    down, across = (grid.ravel() for grid in np.meshgrid(offsets, offsets, indexing="ij"))
    x, y = x[faces], y[faces]
    within = (across[None] < width[faces, None]) & (down[None] < height[faces, None])
    determinant = (y[:, 1] - y[:, 2]) * (x[:, 0] - x[:, 2]) + (x[:, 2] - x[:, 1]) * (y[:, 0] - y[:, 2])
    within &= (np.abs(determinant) >= 1e-12)[:, None]
    face, slot = np.nonzero(within)
    px = first_column[faces][face] + across[slot]
    py = first_row[faces][face] + down[slot]
    x, y, determinant = x[face], y[face], determinant[face]
    first = ((y[:, 1] - y[:, 2]) * (px - x[:, 2]) + (x[:, 2] - x[:, 1]) * (py - y[:, 2])) / determinant
    second = ((y[:, 2] - y[:, 0]) * (px - x[:, 2]) + (x[:, 0] - x[:, 2]) * (py - y[:, 2])) / determinant
    weights = np.stack([first, second, 1.0 - first - second], axis=1)
    inside = np.all(weights >= -1e-9, axis=1)
    return faces[face[inside]], py[inside].astype(np.int64), px[inside].astype(np.int64), weights[inside]


def _large_texels(world, uv, index, size):
    """A face too large to batch, drawn on its own with the same arithmetic."""
    texels = _triangle_texels(world[index], uv[index], np.zeros(3), 0, np.zeros(3), size)
    if texels is None:
        return None
    rows, columns = texels[0].astype(np.int64), texels[1].astype(np.int64)
    x = uv[index, :, 0] * size - 0.5
    y = (1.0 - uv[index, :, 1]) * size - 0.5
    weights = _barycentric(x, y, columns.astype(np.float64), rows.astype(np.float64)).T
    return np.full(len(rows), index), rows, columns, weights


def _triangle_texels(world, uv, normal, owner, colour, size):
    x = uv[:, 0] * size - 0.5
    y = (1.0 - uv[:, 1]) * size - 0.5
    column_range = np.arange(max(0, int(np.floor(x.min()))), min(size - 1, int(np.ceil(x.max()))) + 1)
    row_range = np.arange(max(0, int(np.floor(y.min()))), min(size - 1, int(np.ceil(y.max()))) + 1)
    if not len(column_range) or not len(row_range):
        return None
    columns, rows = np.meshgrid(column_range, row_range)
    weights = _barycentric(x, y, columns.ravel().astype(np.float64), rows.ravel().astype(np.float64))
    if weights is None:
        return None
    inside = np.all(weights >= -1e-9, axis=0)
    if not inside.any():
        return None
    w = weights[:, inside]
    positions = (w[0, :, None] * world[0] + w[1, :, None] * world[1] + w[2, :, None] * world[2]).astype(np.float32)
    count = int(inside.sum())
    return (
        rows.ravel()[inside].astype(np.int32),
        columns.ravel()[inside].astype(np.int32),
        positions,
        np.repeat(normal[None].astype(np.float32), count, axis=0),
        np.full(count, owner, dtype=np.int32),
        np.repeat(np.asarray(colour, dtype=np.float32)[None], count, axis=0),
    )


def _barycentric(x, y, px, py):
    determinant = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
    if abs(determinant) < 1e-12:
        return None
    first = ((y[1] - y[2]) * (px - x[2]) + (x[2] - x[1]) * (py - y[2])) / determinant
    second = ((y[2] - y[0]) * (px - x[2]) + (x[0] - x[2]) * (py - y[2])) / determinant
    return np.stack([first, second, 1.0 - first - second])


def sample_surface(world: np.ndarray, spacing: float, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Points spread over triangles about `spacing` apart, with each point's triangle normal."""
    normals, areas = face_normals(world)
    counts = np.maximum(1, np.rint(areas / spacing ** 2)).astype(np.int64)
    counts[areas <= 1e-10] = 0
    owner = np.repeat(np.arange(len(world)), counts)
    generator = np.random.default_rng(seed)
    first, second = generator.random(len(owner)), generator.random(len(owner))
    root = np.sqrt(first)
    a, b, c = 1 - root, root * (1 - second), root * second
    corners = world[owner]
    points = a[:, None] * corners[:, 0] + b[:, None] * corners[:, 1] + c[:, None] * corners[:, 2]
    return points.astype(np.float32), normals[owner].astype(np.float32)


def pad_gutters(image: np.ndarray, filled: np.ndarray, passes: int = GUTTER_PASSES) -> np.ndarray:
    """Spread island edge colors outward so filtering at chart borders never pulls in the background.

    Used a second way, over the texels photos actually reached, this closes the
    speckle a strict occlusion test leaves behind. A tabletop seen past the
    clutter standing on it keeps every texel the clutter hid, and those texels
    held base colour, so a photographed table came out flecked with brown. A
    few passes fill those specks from their neighbours. It changes only what is
    displayed: the coverage mask still records where photos genuinely landed.

    Each pass fills the unfilled pixels beside a filled one with the mean of
    their filled neighbours, wrapping at the image edge. Only that frontier is
    worked on: redoing the whole 4096-pixel image each pass was most of an
    atlas's image step on the droplet.
    """
    image, filled = image.copy(), filled.copy()
    height, width = filled.shape
    pixels = image.reshape(height * width, -1)
    for _ in range(passes):
        beside = np.roll(filled, 1, axis=0) | np.roll(filled, -1, axis=0) | np.roll(filled, 1, axis=1) | np.roll(filled, -1, axis=1)
        rows, columns = np.nonzero(beside & ~filled)
        if not len(rows):
            break
        total = np.zeros((len(rows), pixels.shape[1]), dtype=image.dtype)
        count = np.zeros(len(rows), dtype=np.float32)
        for neighbour_rows, neighbour_columns in (
            ((rows - 1) % height, columns), ((rows + 1) % height, columns),
            (rows, (columns - 1) % width), (rows, (columns + 1) % width),
        ):
            neighbour_filled = filled[neighbour_rows, neighbour_columns]
            total += pixels[neighbour_rows * width + neighbour_columns] * neighbour_filled[:, None]
            count += neighbour_filled
        pixels[rows * width + columns] = total / count[:, None]
        filled[rows, columns] = True
    return image
