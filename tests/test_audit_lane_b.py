"""Regressions for the Lane B findings in PROGRESS.md.

Each test is a case that returned the wrong answer before its fix.
"""

import math
import uuid
from pathlib import Path

import pytest
import standardphysics_fixtures
from standardphysics_contracts import ClearFloorResult, Mat4, SceneNode, Vec3, to_meters
from standardphysics_fixtures import build_graph, build_scenario, node_id
from standardphysics_pipeline import check_blender
from standardphysics_pipeline.blender import export_glb, glb_node_names
from standardphysics_pipeline.footprints import footprint
from standardphysics_pipeline.ingest import parse_room_json
from standardphysics_pipeline.locus import region_locus
from standardphysics_pipeline.measure import PipelineMeasurements


def _planter_across_leg_zero(distance_m: float, height_m: float) -> SceneNode:
    entrance = build_scenario().stops[0].position
    return SceneNode(
        id=uuid.uuid4(),
        kind="object",
        label="Planter",
        raw_category="storage",
        dimensions=Vec3(x=8.0, y=0.3, z=height_m),
        transform=Mat4.translation(entrance.x, entrance.y + distance_m, height_m / 2),
        movable=True,
    )


def test_a2_blender_from_the_environment_comes_first(monkeypatch, tmp_path):
    fake = tmp_path / "blender"
    fake.write_text("")
    monkeypatch.setenv("BLENDER", str(fake))
    assert check_blender.blender_path() == str(fake)


def test_a2_blender_on_path_is_found(monkeypatch, tmp_path):
    fake = tmp_path / "blender"
    fake.write_text("")
    monkeypatch.delenv("BLENDER", raising=False)
    monkeypatch.setattr(check_blender.shutil, "which", lambda name: str(fake))
    assert check_blender.blender_path() == str(fake)


def test_a5_a_low_solid_obstruction_blocks_the_route():
    graph, scenario = build_graph(), build_scenario()
    graph.nodes.append(_planter_across_leg_zero(1.2, 0.2))
    assert not PipelineMeasurements().route_clear_width(graph, scenario, 0).reachable


def test_a7_resizing_an_object_rebuilds_the_cached_grid():
    graph, scenario = build_graph(), build_scenario()
    measure = PipelineMeasurements()
    measure.route_clear_width(graph, scenario, 0)
    east = graph.by_id(node_id("case_east"))
    east.dimensions.x = 6.0
    east.transform.m[3] = 0.0
    assert not measure.route_clear_width(graph, scenario, 0).reachable


def test_a8_counter_approach_turns_with_the_counter():
    graph = build_graph()
    counter = graph.by_id(node_id("counter"))
    matrix = counter.transform.m
    matrix[0], matrix[1], matrix[4], matrix[5] = 0.0, -1.0, 1.0, 0.0
    centre = PipelineMeasurements().counter_approach(graph, node_id("counter")).center
    xs = [x for x, _ in footprint(counter)]
    ys = [y for _, y in footprint(counter)]
    inside = min(xs) < centre.x < max(xs) and min(ys) < centre.y < max(ys)
    assert not inside


def test_a8_an_unrotated_counter_approach_is_unchanged():
    graph = build_graph()
    counter = graph.by_id(node_id("counter"))
    centre = PipelineMeasurements().counter_approach(graph, node_id("counter")).center
    position = counter.transform.position
    expected_y = position.y - counter.dimensions.y / 2 - to_meters(30.0) / 2
    assert (centre.x, centre.y) == pytest.approx((position.x, expected_y))


def test_a11_a_missing_confidence_needs_another_look():
    element = {
        "identifier": str(uuid.uuid4()),
        "dimensions": [1.0, 1.0, 1.0],
        "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        "category": "table",
    }
    assert parse_room_json({"objects": [element]}).nodes[0].quality == "needs_another_look"


def _node_ids() -> set[str]:
    return {str(node.id) for node in build_graph().nodes}


def test_a12_the_committed_glb_names_every_node_by_id():
    glb = Path(standardphysics_fixtures.__file__).parent / "data" / "shop.glb"
    assert set(glb_node_names(glb)) == _node_ids()


def test_a12_a_fresh_export_names_every_node_by_id(tmp_path):
    try:
        check_blender.blender_path()
    except FileNotFoundError:
        pytest.skip("needs Blender")
    out = export_glb(build_graph(), tmp_path / "scene.glb")
    assert set(glb_node_names(out)) == _node_ids()


def test_a13_a_turned_region_turns_its_outline():
    result = ClearFloorResult(
        inches_wide=48.0, inches_deep=30.0, center=Vec3(x=0, y=0, z=0), fits=True
    )
    corners = region_locus(result, [], rotation=(0.0, 1.0)).annotation.points
    assert max(p.x for p in corners) - min(p.x for p in corners) == pytest.approx(
        to_meters(30.0)
    )


def test_a13_a_turning_space_draws_a_circle():
    result = ClearFloorResult(
        inches_wide=60.0, inches_deep=60.0, center=Vec3(x=1, y=2, z=0), fits=True
    )
    points = region_locus(result, [], circle=True).annotation.points
    radii = [math.hypot(p.x - 1, p.y - 2) for p in points]
    assert radii == pytest.approx([to_meters(30.0)] * len(points))
