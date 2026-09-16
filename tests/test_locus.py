"""Where a finding points, and what gets drawn there."""

import math

import pytest
from standardphysics_contracts import ClearFloorResult, Vec3, to_inches, to_meters
from standardphysics_fixtures import build_graph, build_scenario, node_id
from standardphysics_pipeline.locus import (
    camera_for,
    format_inches,
    path_locus,
    region_locus,
    width_locus,
)
from standardphysics_pipeline.measure import PipelineMeasurements


@pytest.fixture
def pinch():
    graph, scenario = build_graph(), build_scenario()
    result = PipelineMeasurements().route_clear_width(graph, scenario, 0)
    return graph, result


def test_whole_inches_lose_the_decimal():
    assert format_inches(31.0) == "31 in"


def test_a_real_fraction_keeps_one_place():
    assert format_inches(31.44) == "31.4 in"


def test_a_near_miss_still_rounds():
    assert format_inches(35.98) == "36 in"


def test_the_dimension_line_spans_the_measured_gap(pinch):
    graph, result = pinch
    start, end = width_locus(graph, result).annotation.points
    drawn = to_inches(math.dist((start.x, start.y), (end.x, end.y)))
    assert drawn == pytest.approx(result.inches, abs=1e-6)


def test_the_line_attaches_to_the_two_display_cases(pinch):
    graph, result = pinch
    start, end = width_locus(graph, result).annotation.points
    west = graph.by_id(node_id("case_west"))
    east = graph.by_id(node_id("case_east"))
    assert start.x == pytest.approx(west.transform.position.x + west.dimensions.x / 2)
    assert end.x == pytest.approx(east.transform.position.x - east.dimensions.x / 2)


def test_the_label_reads_in_inches(pinch):
    graph, result = pinch
    assert width_locus(graph, result).annotation.label == "31 in"


def test_it_highlights_both_blockers(pinch):
    graph, result = pinch
    assert set(width_locus(graph, result).node_ids) == {
        node_id("case_west"),
        node_id("case_east"),
    }


def test_the_camera_looks_at_the_gap(pinch):
    graph, result = pinch
    locus = width_locus(graph, result)
    assert locus.camera.target.x == pytest.approx(0.0, abs=0.05)


def test_the_camera_watches_from_the_approach(pinch):
    """The customer comes from the door, so the view does too."""
    graph, result = pinch
    locus = width_locus(graph, result)
    assert locus.camera.position.y < locus.camera.target.y


def test_the_camera_is_above_the_floor(pinch):
    graph, result = pinch
    assert width_locus(graph, result).camera.position.z > 1.0


def test_the_bbox_contains_the_drawn_line(pinch):
    graph, result = pinch
    locus = width_locus(graph, result)
    for point in locus.annotation.points:
        assert locus.bbox_min.x <= point.x <= locus.bbox_max.x
        assert locus.bbox_min.y <= point.y <= locus.bbox_max.y


def test_a_bigger_subject_pulls_the_camera_back():
    near = camera_for(Vec3(x=0, y=0, z=0), 1.0, (0.0, -1.0))
    far = camera_for(Vec3(x=0, y=0, z=0), 4.0, (0.0, -1.0))
    assert abs(far.position.y) > abs(near.position.y)


def test_a_tiny_subject_does_not_put_the_camera_inside_it():
    camera = camera_for(Vec3(x=0, y=0, z=0), 0.01, (0.0, -1.0))
    assert math.hypot(camera.position.y, camera.position.z) >= 1.0


def test_a_region_draws_four_corners():
    result = ClearFloorResult(
        inches_wide=60.0, inches_deep=60.0, center=Vec3(x=1, y=2, z=0), fits=True
    )
    locus = region_locus(result, [])
    assert len(locus.annotation.points) == 4


def test_a_region_matches_the_measured_size():
    result = ClearFloorResult(
        inches_wide=60.0, inches_deep=30.0, center=Vec3(x=0, y=0, z=0), fits=False
    )
    corners = region_locus(result, []).annotation.points
    width = max(p.x for p in corners) - min(p.x for p in corners)
    assert width == pytest.approx(to_meters(60.0))


def test_a_path_keeps_every_point(pinch):
    graph, result = pinch
    assert len(path_locus(result).annotation.points) == len(result.path)


def test_a_height_draws_a_vertical_line():
    """A counter height is measured up a face, not across the floor."""
    from standardphysics_pipeline.locus import height_locus

    graph = build_graph()
    counter = graph.by_id(node_id("counter"))
    result = PipelineMeasurements().counter_height(graph, node_id("counter"))
    start, end = height_locus(counter, result).annotation.points
    assert start.z == pytest.approx(0.0)
    assert end.z == pytest.approx(
        counter.transform.position.z + counter.dimensions.z / 2
    )
    assert (start.x, start.y) == pytest.approx((end.x, end.y))


def test_a_height_line_sits_on_the_front_face():
    from standardphysics_pipeline.locus import height_locus

    graph = build_graph()
    counter = graph.by_id(node_id("counter"))
    result = PipelineMeasurements().counter_height(graph, node_id("counter"))
    start, _ = height_locus(counter, result).annotation.points
    front = counter.transform.position.y - counter.dimensions.y / 2
    assert start.y == pytest.approx(front)


def test_a_height_camera_stands_beside_it_not_above():
    """From overhead a vertical line is a dot."""
    from standardphysics_pipeline.locus import height_locus

    graph = build_graph()
    counter = graph.by_id(node_id("counter"))
    result = PipelineMeasurements().counter_height(graph, node_id("counter"))
    locus = height_locus(counter, result)
    top = counter.transform.position.z + counter.dimensions.z / 2
    assert locus.camera.position.z < top * 2
