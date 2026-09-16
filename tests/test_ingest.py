"""Parsing a CapturedRoom export.

Written against the documented shape. Until a real room.json lands from Lane A
these prove the parser is self-consistent, not that it matches the SDK.
"""

import uuid

import pytest
from standardphysics_pipeline.ingest import (
    RoomParseError,
    missing_coverage,
    parse_room_json,
    to_arkit_columns,
)


def column_major(x: float, y: float, z: float) -> list[float]:
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1]


def element(category, confidence="high", dims=(1.0, 2.0, 0.5), at=(0.0, 0.0, 0.0)):
    return {
        "identifier": str(uuid.uuid4()),
        "dimensions": list(dims),
        "transform": column_major(*at),
        "category": category,
        "confidence": confidence,
    }


def test_reads_a_bare_string_category():
    graph = parse_room_json({"objects": [element("table")]})
    assert graph.nodes[0].raw_category == "table"


def test_reads_a_single_key_object_category():
    graph = parse_room_json({"objects": [element({"table": {}})]})
    assert graph.nodes[0].raw_category == "table"


def test_reads_a_category_carrying_an_associated_value():
    """Surface.Category.door(isOpen:) encodes its payload alongside the name."""
    graph = parse_room_json({"doors": [element({"door": {"isOpen": False}})]})
    assert graph.nodes[0].raw_category == "door"


def test_high_confidence_is_measured():
    graph = parse_room_json({"objects": [element("table", {"high": {}})]})
    assert graph.nodes[0].quality == "measured"


def test_medium_confidence_asks_for_another_look():
    graph = parse_room_json({"objects": [element("table", "medium")]})
    assert graph.nodes[0].quality == "needs_another_look"


def test_plumbed_in_objects_do_not_start_movable():
    graph = parse_room_json({"objects": [element("refrigerator")]})
    assert not graph.nodes[0].movable


def test_furniture_starts_movable():
    graph = parse_room_json({"objects": [element("chair")]})
    assert graph.nodes[0].movable


def test_walls_never_start_movable():
    graph = parse_room_json({"walls": [element("wall")]})
    assert not graph.nodes[0].movable


def test_height_becomes_z():
    graph = parse_room_json({"objects": [element("table", dims=(3.2, 1.1, 0.7))]})
    dims = graph.nodes[0].dimensions
    assert (dims.x, dims.y, dims.z) == (3.2, 0.7, 1.1)


def test_position_converts_out_of_y_up():
    graph = parse_room_json({"objects": [element("table", at=(1.0, 2.0, 3.0))]})
    position = graph.nodes[0].transform.position
    assert (position.x, position.y, position.z) == (1.0, -3.0, 2.0)


def test_every_surface_kind_is_read():
    payload = {
        "walls": [element("wall")],
        "doors": [element("door")],
        "windows": [element("window")],
        "openings": [element("opening")],
        "floors": [element("floor")],
        "objects": [element("table")],
    }
    kinds = {node.kind for node in parse_room_json(payload).nodes}
    assert kinds == {"wall", "door", "window", "opening", "floor", "object"}


def test_transform_round_trips_through_arkit_layout():
    graph = parse_room_json({"objects": [element("table", at=(1.5, 0.5, -2.0))]})
    assert to_arkit_columns(graph.nodes[0]) == pytest.approx(
        column_major(1.5, 0.5, -2.0)
    )


def test_uncertain_nodes_are_listed_for_a_rescan():
    payload = {"objects": [element("table", "low"), element("chair", "high")]}
    assert len(missing_coverage(parse_room_json(payload))) == 1


def test_an_empty_export_is_an_error_not_an_empty_room():
    with pytest.raises(RoomParseError):
        parse_room_json({"walls": [], "objects": []})


def test_a_short_transform_is_rejected():
    bad = element("table")
    bad["transform"] = [1, 0, 0, 0]
    with pytest.raises(RoomParseError):
        parse_room_json({"objects": [bad]})


def test_an_unreadable_category_is_rejected():
    bad = element({"a": {}, "b": {}})
    with pytest.raises(RoomParseError):
        parse_room_json({"objects": [bad]})
