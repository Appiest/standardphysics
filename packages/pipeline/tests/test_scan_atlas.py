"""Baking photographs into an atlas over the painted scan."""

from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import numpy as np
import pytest
from standardphysics_pipeline.check_blender import blender_path
from standardphysics_pipeline.coords import capture_to_room
from standardphysics_pipeline.ingest import parse_room_json
from standardphysics_pipeline.textures.hole_patches import with_holes_patched
from standardphysics_pipeline.textures.project import face_normals, rasterize_atlas
from standardphysics_pipeline.textures.scan_atlas import (
    TEXEL_METRES,
    _corner_weights,
    _face_filled,
    _with_every_face_owned,
    agreed_colours,
    atlas_size,
    unwrapped,
)
from standardphysics_pipeline.textures.scan_colour import SEEN_TOLERANCE, ColouredScan, _seen_tolerance, scan_geometry

REPO = pathlib.Path(__file__).resolve().parents[3]


def _blender_missing() -> bool:
    try:
        blender_path()
    except (FileNotFoundError, RuntimeError):
        return True
    return False


@pytest.mark.skipif(_blender_missing(), reason="Blender not installed")
def test_a_real_lidar_mesh_unwraps_to_texels_a_few_centimetres_across(tmp_path):
    vertices, triangles = scan_geometry(REPO / "datasets/phone/test1/lidar-mesh.json", capture_to_room(0.0))
    scan = ColouredScan(vertices, triangles, np.zeros((len(vertices), 3)), np.zeros(len(vertices), bool))

    mesh = unwrapped(scan, tmp_path, max_triangles=260_000)
    size = atlas_size(mesh)

    _, world_areas = face_normals(mesh.corners)
    first, second = mesh.uv[:, 1] - mesh.uv[:, 0], mesh.uv[:, 2] - mesh.uv[:, 0]
    texels = (np.abs(first[:, 0] * second[:, 1] - first[:, 1] * second[:, 0]) / 2).sum() * size ** 2
    assert np.sqrt(world_areas.sum() / texels) <= 1.5 * TEXEL_METRES


def test_one_photo_of_a_passer_by_loses_to_four_photos_of_the_floor():
    floor, person = np.array([0.30, 0.28, 0.25]), np.array([0.05, 0.05, 0.40])
    weights = np.array([[0.9, 0.5, 0.45, 0.4, 0.35]], dtype=np.float32)
    colours = np.array([[person, floor, floor + 0.01, floor - 0.01, floor]], dtype=np.float32)

    assert agreed_colours(weights, colours)[0] == pytest.approx(floor, abs=0.02)


def test_where_every_view_agrees_the_best_view_dominates():
    weights = np.array([[0.9, 0.3, 0.0, 0.0, 0.0]], dtype=np.float32)
    colours = np.array([[[0.50, 0.50, 0.50], [0.54, 0.54, 0.54], [0, 0, 0], [0, 0, 0], [0, 0, 0]]], dtype=np.float32)

    assert agreed_colours(weights, colours)[0] == pytest.approx([0.50, 0.50, 0.50], abs=0.005)


@pytest.mark.skipif(_blender_missing(), reason="Blender not installed")
def test_thinning_a_patched_scan_keeps_its_surface(tmp_path):
    """Hole patches arrive as separate squares; thinned unwelded, they broke into a lattice with gaps."""
    graph = parse_room_json(json.loads((REPO / "datasets/phone/test1/room.json").read_text()))
    vertices, triangles = scan_geometry(REPO / "datasets/phone/test1/lidar-mesh.json", graph.capture_to_room)
    patched = with_holes_patched(vertices, triangles, graph)
    scan = ColouredScan(patched.vertices, patched.triangles, np.zeros((len(patched.vertices), 3)), np.zeros(len(patched.vertices), bool))
    _, before = face_normals(patched.vertices[patched.triangles])

    mesh = unwrapped(scan, tmp_path, max_triangles=len(patched.triangles) // 9)
    _, after = face_normals(mesh.corners)

    assert after.sum() >= 0.97 * before.sum()


def test_a_surface_seen_at_a_slant_is_allowed_the_depth_it_spans_across_a_buffer_pixel():
    camera = SimpleNamespace(width=1600, fx=1100.0)
    depth, facing = np.array([4.0, 4.0]), np.array([1.0, 0.3])
    face_on, slanted = _seen_tolerance(camera, 240, depth, facing, slope_aware=True)
    assert face_on == pytest.approx(SEEN_TOLERANCE)
    assert slanted > 3 * SEEN_TOLERANCE
    assert _seen_tolerance(camera, 240, depth, facing, slope_aware=False) == SEEN_TOLERANCE


def test_a_texel_no_photo_reached_takes_its_surface_colour_and_never_a_neighbours():
    yellow, grey = [0.8, 0.6, 0.1], [0.5, 0.5, 0.5]
    vertices = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [5, 0, 0], [6, 0, 0], [5, 1, 0]], dtype=np.float64)
    mesh = SimpleNamespace(vertices=vertices, triangles=np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6]]))
    surface = SimpleNamespace(
        mesh=mesh,
        faces=np.array([0, 0, 1, 2]),
        positions=np.array([[0.6, 0.2, 0], [0.8, 0.3, 0], [0.2, 0.7, 0], [5.2, 0.2, 0]], dtype=np.float32),
        fallback=np.array([grey] * 4, dtype=np.float32),
    )
    surface.corner_weights = _corner_weights(mesh, surface.faces, surface.positions)
    colours = np.array([yellow, yellow, [0, 0, 0], [0, 0, 0]], dtype=np.float32)
    painted = np.array([True, True, False, False])

    filled = _face_filled(surface, colours, painted)

    assert filled[2] == pytest.approx(yellow), "the face beside a photographed one takes its colour along the surface"
    assert filled[3] == pytest.approx(grey), "a face no photograph reaches along the surface keeps the room's material"


@pytest.mark.skipif(_blender_missing(), reason="Blender not installed")
def test_every_face_of_a_real_scan_is_drawn_from_texels_baked_for_it(tmp_path):
    """A face owning no texel is drawn from whatever sits under it in the atlas, a speck of another surface."""
    graph = parse_room_json(json.loads((REPO / "datasets/phone/test1/room.json").read_text()))
    vertices, triangles = scan_geometry(REPO / "datasets/phone/test1/lidar-mesh.json", graph.capture_to_room)
    patched = with_holes_patched(vertices, triangles, graph)
    scan = ColouredScan(patched.vertices, patched.triangles, np.zeros((len(patched.vertices), 3)), np.zeros(len(patched.vertices), bool))
    mesh = unwrapped(scan, tmp_path, max_triangles=len(patched.triangles) // 9)
    size = atlas_size(mesh) // 2
    texels = rasterize_atlas(mesh.corners, mesh.uv, np.arange(len(mesh.triangles), dtype=np.int32), size)
    assert np.bincount(texels.owners, minlength=len(mesh.triangles)).min() == 0, "as crowded as a library walk, some faces cover no texel centre"

    owned = _with_every_face_owned(mesh, size, texels.rows, texels.columns, texels.owners, texels.positions, texels.normals)

    assert np.bincount(owned[2], minlength=len(mesh.triangles)).min() >= 1
    assert len(np.unique(owned[0].astype(np.int64) * size + owned[1])) == len(owned[0]), "no texel belongs to two faces"


def _uv_area(mesh) -> np.ndarray:
    first, second = mesh.uv[:, 1] - mesh.uv[:, 0], mesh.uv[:, 2] - mesh.uv[:, 0]
    return np.abs(first[:, 0] * second[:, 1] - first[:, 1] * second[:, 0]) / 2


@pytest.mark.skipif(_blender_missing(), reason="Blender not installed")
def test_a_budget_packed_into_two_atlases_gives_every_face_at_least_twice_the_texture(tmp_path):
    """A library floor in one atlas left each face a texel or two, and it rendered as flat-coloured shards."""
    vertices, triangles = scan_geometry(REPO / "datasets/phone/test1/lidar-mesh.json", capture_to_room(0.0))
    scan = ColouredScan(vertices, triangles, np.zeros((len(vertices), 3)), np.zeros(len(vertices), bool))
    budget = len(triangles) // 4

    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    one = unwrapped(scan, tmp_path / "one", max_triangles=budget, atlases=1)
    two = unwrapped(scan, tmp_path / "two", max_triangles=budget, atlases=2)

    assert one.atlas_count == 1 and two.atlas_count == 2
    assert sorted(np.bincount(two.atlases).tolist()) == pytest.approx([len(two.triangles) / 2] * 2, rel=0.01)
    for index in range(2):
        packed = two.atlas(index)
        assert packed.uv.min() >= 0.0 and packed.uv.max() <= 1.0
    per_face_one = _uv_area(one).sum() / len(one.triangles)
    per_face_two = sum(_uv_area(two.atlas(index)).sum() for index in range(2)) / len(two.triangles)
    assert per_face_two >= 1.8 * per_face_one


def _rasterized_face_by_face(world, uv, owners, size):
    """The atlas rasterizer as it was: one call per face, the later face winning each texel."""
    from standardphysics_pipeline.textures.project import _triangle_texels

    normals, areas = face_normals(world)
    colours = np.full((len(world), 3), 0.65, dtype=np.float32)
    pieces = [_triangle_texels(world[i], uv[i], normals[i], owners[i], colours[i], size) for i in np.flatnonzero(areas > 1e-10)]
    rows, columns, positions, normals_out, owners_out, _ = (np.concatenate(parts) for parts in zip(*[p for p in pieces if p is not None]))
    _, last = np.unique((rows.astype(np.int64) * size + columns)[::-1], return_index=True)
    keep = len(rows) - 1 - last
    return rows[keep], columns[keep], positions[keep], normals_out[keep], owners_out[keep]


@pytest.mark.skipif(_blender_missing(), reason="Blender not installed")
def test_drawing_small_faces_together_matches_drawing_every_face_alone(tmp_path):
    vertices, triangles = scan_geometry(REPO / "datasets/phone/test1/lidar-mesh.json", capture_to_room(0.0))
    scan = ColouredScan(vertices, triangles, np.zeros((len(vertices), 3)), np.zeros(len(vertices), bool))
    mesh = unwrapped(scan, tmp_path, max_triangles=260_000)
    size = atlas_size(mesh)
    owners = np.arange(len(mesh.triangles), dtype=np.int32)

    together = rasterize_atlas(mesh.corners, mesh.uv, owners, size)
    alone = _rasterized_face_by_face(mesh.corners, mesh.uv, owners, size)

    assert len(together.rows) > 100_000
    for batched, reference in zip((together.rows, together.columns, together.positions, together.normals, together.owners), alone):
        assert np.array_equal(batched, reference)


def _padded_whole_image(image, filled, passes):
    """Gutter padding as it was: every pass over the whole image."""
    image, filled = image.copy(), filled.copy()
    for _ in range(passes):
        total = np.zeros_like(image)
        count = np.zeros(filled.shape, dtype=np.float32)
        for axis, step in ((0, 1), (0, -1), (1, 1), (1, -1)):
            shifted_filled = np.roll(filled, step, axis=axis)
            total += np.roll(image, step, axis=axis) * shifted_filled[..., None]
            count += shifted_filled
        grow = ~filled & (count > 0)
        image[grow] = total[grow] / count[grow][:, None]
        filled |= grow
    return image


@pytest.mark.skipif(_blender_missing(), reason="Blender not installed")
def test_padding_only_the_frontier_matches_padding_the_whole_image(tmp_path):
    from standardphysics_pipeline.textures.project import GUTTER_PASSES, pad_gutters

    vertices, triangles = scan_geometry(REPO / "datasets/phone/test1/lidar-mesh.json", capture_to_room(0.0))
    scan = ColouredScan(vertices, triangles, np.zeros((len(vertices), 3)), np.zeros(len(vertices), bool))
    mesh = unwrapped(scan, tmp_path, max_triangles=260_000)
    size = atlas_size(mesh)
    texels = rasterize_atlas(mesh.corners, mesh.uv, np.arange(len(mesh.triangles), dtype=np.int32), size)
    image = np.zeros((size, size, 3), dtype=np.float32)
    image[texels.rows, texels.columns] = np.abs(texels.positions) % 1.0
    filled = np.zeros((size, size), dtype=bool)
    filled[texels.rows, texels.columns] = True

    assert np.array_equal(pad_gutters(image, filled), _padded_whole_image(image, filled, GUTTER_PASSES))
