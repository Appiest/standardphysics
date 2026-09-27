"""Filling unphotographed walls and floors with a generated material.

The room is a four metre floor with one wall standing on its far edge, both as
real measured sheets, so which vertex belongs to which surface is geometry.
"""

from __future__ import annotations

import json
import uuid

import numpy as np
import pytest
from PIL import Image
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_pipeline.textures.project import to_linear
from standardphysics_pipeline.textures.scan_colour import ColouredScan, vertex_normals
from standardphysics_pipeline.textures.surface_materials import (
    MANIFEST,
    MaterialFill,
    SurfaceMaterial,
    _wrapped_bilinear,
    load_materials,
    material_key,
    materials_digest,
    planar_sample,
    room_owners,
    unseen_surfaces_filled,
)

WALL, FLOOR, TELEVISION = 0, 1, 2


def sheet(label: str, size: tuple[float, float, float], at: tuple[float, float, float]) -> SceneNode:
    return SceneNode(
        id=uuid.uuid4(), kind=label.lower(), label=label, raw_category=label.lower(),
        dimensions=Vec3(x=size[0], y=size[1], z=size[2]), transform=Mat4.translation(*at),
    )


def room() -> SceneGraph:
    return SceneGraph(scan_id=uuid.uuid4(), nodes=[
        sheet("Wall", (4.0, 0.0, 3.0), (0.0, 2.0, 1.5)),
        sheet("Floor", (4.0, 4.0, 0.0), (0.0, 0.0, 0.0)),
        sheet("Television", (1.2, 0.08, 0.7), (1.4, 1.94, 1.5)),
    ])


def patch(origin, across, up, steps: int = 6) -> tuple[np.ndarray, np.ndarray]:
    """A grid of vertices facing along across x up, triangulated so the normals agree."""
    origin, across, up = (np.asarray(value, dtype=float) for value in (origin, across, up))
    grid = np.array([origin + across * i / steps + up * j / steps for j in range(steps + 1) for i in range(steps + 1)])
    row = steps + 1
    triangles = []
    for j in range(steps):
        for i in range(steps):
            corner = j * row + i
            triangles += [[corner, corner + 1, corner + row + 1], [corner, corner + row + 1, corner + row]]
    return grid, np.asarray(triangles)


def joined(*patches) -> tuple[np.ndarray, np.ndarray]:
    vertices, triangles, offset = [], [], 0
    for grid, faces in patches:
        vertices.append(grid)
        triangles.append(faces + offset)
        offset += len(grid)
    return np.concatenate(vertices), np.concatenate(triangles)


def wall_patch():
    return patch((-1.0, 1.98, 0.5), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), steps=12)


def floor_patch():
    return patch((-1.0, -1.0, 0.01), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))


def flat_material(value: float = 0.5) -> SurfaceMaterial:
    return SurfaceMaterial(np.full((8, 8, 3), value, dtype=np.float32), metres_across=1.0)


def owners_of(vertices, triangles):
    return room_owners(vertices, vertex_normals(vertices, triangles), room())


def test_wall_and_floor_vertices_are_told_apart():
    vertices, triangles = joined(wall_patch(), floor_patch())
    owners = owners_of(vertices, triangles)
    wall_count = len(wall_patch()[0])
    assert (owners[:wall_count] == WALL).all()
    assert (owners[wall_count:] == FLOOR).all()


def test_a_television_on_the_wall_is_the_television():
    vertices, triangles = patch((1.0, 1.9, 1.3), (0.8, 0.0, 0.0), (0.0, 0.0, 0.4))
    assert (owners_of(vertices, triangles) == TELEVISION).all()


def test_a_shelf_top_inside_the_wall_box_is_not_the_wall():
    vertices, triangles = patch((-1.0, 1.9, 1.2), (1.0, 0.0, 0.0), (0.0, 0.08, 0.0))
    assert (owners_of(vertices, triangles) == -1).all()


def test_the_underside_of_something_on_the_floor_is_not_the_floor():
    vertices, triangles = patch((-1.0, -1.0, 0.05), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0))
    assert (owners_of(vertices, triangles) == -1).all()


def test_things_away_from_every_sheet_are_left_alone():
    vertices, triangles = patch((-0.5, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    assert (owners_of(vertices, triangles) == -1).all()


def test_material_keys_name_what_a_node_is():
    assert [material_key(node) for node in room().nodes] == ["wall", "floor", "television"]


@pytest.mark.parametrize("label", ["Outlet", "Outlet (electrical outlet)", "Candidate outlet (power outlet)"])
def test_every_name_for_an_outlet_shares_one_material(label):
    assert material_key(sheet(label, (0.12, 0.03, 0.12), (0.0, 1.98, 0.4))) == "outlet"


def half_seen_wall(photo_colour: float = 0.7) -> ColouredScan:
    vertices, triangles = wall_patch()
    seen = vertices[:, 0] < 0.0
    colours = np.where(seen[:, None], photo_colour, 0.62).astype(np.float32)
    return ColouredScan(vertices, triangles, np.broadcast_to(colours, (len(vertices), 3)).copy(), seen)


def test_the_fill_takes_the_photographed_colour_of_its_own_wall():
    scan = half_seen_wall(photo_colour=0.7)
    filled = unseen_surfaces_filled(scan, room(), {"wall": flat_material()})
    np.testing.assert_allclose(filled.colours[~scan.seen], 0.7, atol=1e-3)


def test_photographed_vertices_and_coverage_are_never_changed():
    scan = half_seen_wall()
    filled = unseen_surfaces_filled(scan, room(), {"wall": flat_material(0.2)})
    np.testing.assert_array_equal(filled.colours[scan.seen], scan.colours[scan.seen])
    np.testing.assert_array_equal(filled.seen, scan.seen)
    assert filled.painted_fraction == scan.painted_fraction


def test_without_materials_the_scan_is_returned_unchanged():
    scan = half_seen_wall()
    assert unseen_surfaces_filled(scan, room(), {}) is scan


def test_the_pattern_repeats_at_its_real_size():
    tile = np.random.default_rng(3).random((16, 16, 3)).astype(np.float32)
    material = SurfaceMaterial(tile, metres_across=0.5)
    facing_wall = np.tile([0.0, -1.0, 0.0], (2, 1))
    points = np.array([[0.13, 2.0, 1.07], [0.63, 2.0, 1.57]])
    first, second = planar_sample(material, points, facing_wall)
    np.testing.assert_allclose(first, second, atol=1e-5)


def test_a_missing_manifest_means_no_materials(tmp_path):
    assert load_materials(tmp_path) == {}


def write_material(directory, key, value):
    directory.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.full((4, 4, 3), value, dtype=np.uint8), "RGB").save(directory / f"{key}.png")
    manifest = directory / MANIFEST
    entries = json.loads(manifest.read_text()) if manifest.is_file() else {}
    entries[key] = {"image": f"{key}.png", "metres_across": 1.0}
    manifest.write_text(json.dumps(entries))


def test_a_room_material_overrides_the_generic_one_key_by_key(tmp_path):
    write_material(tmp_path / "generic", "wall", 50)
    write_material(tmp_path / "generic", "floor", 60)
    write_material(tmp_path / "room", "wall", 200)
    materials = load_materials(tmp_path / "generic", tmp_path / "room")
    assert float(materials["wall"].tile.mean()) > float(materials["floor"].tile.mean())


def test_new_room_materials_change_the_fingerprint(tmp_path):
    assert materials_digest(tmp_path) == ""
    write_material(tmp_path, "wall", 50)
    first = materials_digest(tmp_path)
    write_material(tmp_path, "wall", 51)
    assert first and materials_digest(tmp_path) != first


def test_the_same_fill_paints_atlas_texels():
    owners = np.array([0, 0, 0, 1])
    photographed = np.array([True, False, False, False])
    colours = np.full((4, 3), 0.1, dtype=np.float32)
    fill = MaterialFill(["wall", "chair"], {"wall": flat_material(0.5)})
    positions = np.zeros((4, 3))
    normals = np.tile([0.0, 1.0, 0.0], (4, 1))
    filled = fill.apply(colours, photographed, positions, normals, owners)
    np.testing.assert_allclose(filled[1:3], 0.5, atol=1e-6)
    np.testing.assert_array_equal(filled[[0, 3]], colours[[0, 3]])


@pytest.mark.parametrize("kind", ["wall", "floor"])
def test_the_shipped_tiles_load_as_linear_colour(kind):
    material = load_materials()[kind]
    assert material.tile.ndim == 3 and material.tile.shape[2] == 3
    assert material.metres_across > 0
    assert 0.0 < float(material.tile.mean()) < float(to_linear(np.array(1.0)))


def test_a_room_material_keeps_its_own_colour():
    owners = np.array([0, 0])
    photographed = np.array([True, False])
    colours = np.full((2, 3), 0.05, dtype=np.float32)
    own = SurfaceMaterial(np.full((8, 8, 3), 0.6, dtype=np.float32), metres_across=1.0, tint=False)
    filled = MaterialFill(["wall"], {"wall": own}).apply(
        colours, photographed, np.zeros((2, 3)), np.tile([0.0, 1.0, 0.0], (2, 1)), owners,
    )
    np.testing.assert_allclose(filled[1], 0.6, atol=1e-6)


def test_a_point_a_hair_below_a_tile_edge_samples_inside_the_tile():
    """In float32 a tiny negative coordinate wraps to exactly the tile's width, one past its last pixel."""
    tile = np.arange(512 * 512 * 3, dtype=np.float32).reshape(512, 512, 3)
    edge = np.array([-1e-9], dtype=np.float32)
    assert _wrapped_bilinear(tile, edge, edge).shape == (1, 3)
