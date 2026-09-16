"""Lane B against real RoomPlan exports.

Everything else in this lane is checked against geometry we built ourselves,
which proves the maths and not the assumptions. These two rooms came off a real
phone and settle the assumptions.
"""

import json
import pathlib
import plistlib

import pytest
from standardphysics_contracts import to_meters
from standardphysics_pipeline.blender import usdz_to_glb
from standardphysics_pipeline.check_blender import blender_path
from standardphysics_pipeline.ingest import parse_room_json
from standardphysics_pipeline.occupancy import CANE_DETECTABLE, blocks_floor

REAL = (
    pathlib.Path(__file__).parent.parent
    / "packages/fixtures/standardphysics_fixtures/data/real"
)
ROOMS = ["apple_livingroom", "apple_bedroom3"]


def blender_missing() -> bool:
    try:
        blender_path()
    except FileNotFoundError:
        return True
    return False


def load(name: str):
    return parse_room_json(json.loads((REAL / f"{name}.room.json").read_text()))


@pytest.mark.parametrize("name", ROOMS)
def test_a_real_export_parses(name):
    assert load(name).nodes


@pytest.mark.parametrize("name", ROOMS)
def test_the_floor_ends_up_at_zero(name):
    """RoomPlan's origin is wherever the phone started, about chest height, so
    a raw scan puts its floor near z = -1.4."""
    floor = next(node for node in load(name).nodes if node.kind == "floor")
    assert floor.transform.position.z == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("name", ROOMS)
def test_furniture_stands_on_the_floor_not_under_it(name):
    graph = load(name)
    standing = [
        node
        for node in graph.nodes
        if node.kind == "object"
        and node.transform.position.z - node.dimensions.z / 2 < 0.05
    ]
    assert standing
    assert all(blocks_floor(node) for node in standing)


def test_a_wall_mounted_cabinet_is_not_a_floor_obstruction():
    """Its top is high, but nothing on the floor has to go around it. ADA 2010
    307 handles it as a protruding object instead."""
    graph = load("apple_livingroom")
    mounted = [
        node
        for node in graph.nodes
        if node.kind == "object"
        and node.transform.position.z - node.dimensions.z / 2 > CANE_DETECTABLE
    ]
    assert mounted
    assert not any(blocks_floor(node) for node in mounted)


def test_something_high_up_still_blocks_if_it_reaches_the_floor():
    graph = load("apple_livingroom")
    tall = [
        node
        for node in graph.nodes
        if node.kind == "object"
        and node.dimensions.z > to_meters(60.0)
        and node.transform.position.z - node.dimensions.z / 2 < 0.05
    ]
    assert all(blocks_floor(node) for node in tall)


@pytest.mark.parametrize("name", ROOMS)
def test_openings_know_which_wall_they_were_cut_into(name):
    graph = load(name)
    cut = [n for n in graph.nodes if n.kind in ("door", "window", "opening")]
    assert cut
    assert all(node.parent_id is not None for node in cut)


@pytest.mark.parametrize("name", ROOMS)
def test_the_mapping_file_is_a_binary_plist(name):
    """Lane A names it .json. A real one starts with bplist00 and json.loads
    throws on it."""
    raw = (REAL / f"{name}.metadata.plist").read_bytes()
    assert raw[:8] == b"bplist00"
    assert plistlib.loads(raw)


@pytest.mark.parametrize("name", ROOMS)
def test_an_unknown_top_level_key_is_not_fatal(name):
    """Real exports carry coreModel, sections, story and version, and no
    top-level identifier."""
    raw = json.loads((REAL / f"{name}.room.json").read_text())
    assert "coreModel" in raw
    assert "identifier" not in raw
    assert parse_room_json(raw).nodes


@pytest.mark.skipif(blender_missing(), reason="Blender not installed")
@pytest.mark.parametrize("name", ROOMS)
def test_a_real_usdz_converts_with_every_mesh_identified(tmp_path, name):
    result = usdz_to_glb(
        REAL / f"{name}.usdz",
        tmp_path / f"{name}.glb",
        REAL / f"{name}.metadata.plist",
    )
    assert result.fully_identified
    assert result.renamed == result.meshes


PHONE = pathlib.Path(__file__).parent.parent / "datasets/phone"
PHONE_SCANS = ["test1", "ravida"]


def phone_missing(name: str) -> bool:
    return not (PHONE / name / "room.json").exists()


@pytest.mark.parametrize("name", PHONE_SCANS)
def test_a_scan_off_our_own_phone_parses(name):
    if phone_missing(name):
        pytest.skip(f"{name} not present")
    assert parse_room_json(json.loads((PHONE / name / "room.json").read_text())).nodes


@pytest.mark.parametrize("name", PHONE_SCANS)
def test_a_phone_scan_stands_on_its_floor(name):
    if phone_missing(name):
        pytest.skip(f"{name} not present")
    graph = parse_room_json(json.loads((PHONE / name / "room.json").read_text()))
    floor = next(node for node in graph.nodes if node.kind == "floor")
    assert floor.transform.position.z == pytest.approx(0.0, abs=1e-6)


@pytest.mark.skipif(blender_missing(), reason="Blender not installed")
@pytest.mark.parametrize("name", PHONE_SCANS)
def test_a_phone_usdz_keeps_every_mesh_identified(tmp_path, name):
    """A real export carries each element twice, parametric and mesh, so the
    second import of Chair0 arrives as Chair0.001. That suffix is Blender
    disambiguating rather than part of the USD name, and taking it literally
    lost half the identities on the first phone scan."""
    if phone_missing(name):
        pytest.skip(f"{name} not present")
    result = usdz_to_glb(
        PHONE / name / "room.usdz",
        tmp_path / f"{name}.glb",
        PHONE / name / "room.metadata.plist",
    )
    assert result.fully_identified
    assert result.renamed == result.meshes
