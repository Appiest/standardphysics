"""Shared fixtures for Lane C.

The shop, the rule pack and the ledger are immutable, and building an
occupancy grid is not free, so they are built once for the session. Tests that
need a different layout copy one rather than editing these.

The shipped verification ledger is empty, because a person has to read each
section before its check runs. Tests need the checks to run, so they build
their own ledger and say so in its `verified_by`. Nothing in this directory
writes the shipped one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from standardphysics_agents import VerificationLedger, load_pack
from standardphysics_contracts import Mat4, Scenario, SceneGraph, SceneNode, Stop, Vec3, to_meters
from standardphysics_fixtures import FixtureMeasurements, build_graph, build_scenario, node_id
from standardphysics_pipeline import PipelineMeasurements

TEST_REVIEWER = "test suite, not a person"


@pytest.fixture(scope="session")
def pack():
    return load_pack()


@pytest.fixture(scope="session")
def ledger(pack):
    """Every rule verified, so every check runs."""
    book = VerificationLedger()
    for rule in pack.rules:
        book = book.record(rule, verified_by=TEST_REVIEWER)
    return book


@pytest.fixture(scope="session")
def graph():
    return build_graph()


@pytest.fixture(scope="session")
def scenario():
    return build_scenario()


@pytest.fixture(scope="session")
def stub():
    return FixtureMeasurements()


@pytest.fixture(scope="session")
def pipeline():
    return PipelineMeasurements()


@pytest.fixture(scope="session", params=["stub", "pipeline"])
def measure(request):
    """Both providers, so a check that only works against one is caught."""
    return {"stub": FixtureMeasurements(), "pipeline": PipelineMeasurements()}[
        request.param
    ]


CORNER_CAFE_PINCH_INCHES = 31.0


def _cafe_piece(name, label, centre, dims, movable, kind="object"):
    return SceneNode(
        id=node_id(f"corner_cafe_{name}"),
        kind=kind,
        label=label,
        raw_category=kind,
        dimensions=Vec3(x=dims[0], y=dims[1], z=dims[2]),
        transform=Mat4.translation(*centre),
        movable=movable,
    )


@pytest.fixture(scope="session")
def corner_cafe():
    """A narrow cafe whose only table pinches the aisle to the counter to 31 inches.

    The inner walls stand at x = ±1.5 and y = -2.5 and 2.0. The north-west
    corner is tight: the counter runs along the north wall and a display case
    stands against the west wall just south of the corner, so a table shoved
    flush into that corner has a wall on two sides, the counter across a
    third and the case across the fourth. Sliding the table a few inches west
    instead opens the aisle and leaves open floor south of it.
    """
    table_east_edge = 0.8 - to_meters(CORNER_CAFE_PINCH_INCHES)
    nodes = [
        _cafe_piece("floor", "Floor", (0.0, -0.25, 0.0), (3.1, 4.6, 0.01), False, kind="floor"),
        _cafe_piece("wall_west", "Wall", (-1.55, -0.25, 1.5), (0.1, 4.6, 3.0), False, kind="wall"),
        _cafe_piece("wall_east", "Wall", (1.55, -0.25, 1.5), (0.1, 4.6, 3.0), False, kind="wall"),
        _cafe_piece("wall_south", "Wall", (0.0, -2.55, 1.5), (3.2, 0.1, 3.0), False, kind="wall"),
        _cafe_piece("wall_north", "Wall", (0.0, 2.05, 1.5), (3.2, 0.1, 3.0), False, kind="wall"),
        _cafe_piece("counter", "Ordering counter", (0.5, 1.7, 0.5), (2.0, 0.6, 1.0), False),
        _cafe_piece("shelf", "Shelf", (1.15, 0.1, 0.6), (0.7, 1.0, 1.2), False),
        _cafe_piece("case", "Display case", (-1.25, 0.1, 0.45), (0.5, 0.8, 0.9), False),
        _cafe_piece("table", "Table", (table_east_edge - 0.4, 0.3, 0.375), (0.8, 1.2, 0.75), True),
    ]
    graph = SceneGraph(scan_id=node_id("corner_cafe"), nodes=nodes)
    scenario = Scenario(
        name="Order at the counter",
        stops=[
            Stop(name="Entrance", position=Vec3(x=0.3, y=-2.2, z=0.0), anchor_node_id=node_id("corner_cafe_wall_south")),
            Stop(name="Counter", position=Vec3(x=0.5, y=1.0, z=0.0), anchor_node_id=node_id("corner_cafe_counter")),
        ],
    )
    return graph, scenario


def captured_room() -> tuple[SceneGraph, Scenario]:
    """The captured placement room, with its dead-end stop taken as a restroom so 304.3 applies there.

    A turning space is only required in a room whose own section calls for one,
    and the capture's dead end is a seat, which none does.
    """
    data = json.loads((Path(__file__).parent / "fixtures/placement-room.json").read_text())
    scenario = Scenario.model_validate(data["scenario"])
    stops = [stop.model_copy(update={"name": "Restroom"}) if stop.name == "Seat" else stop for stop in scenario.stops]
    return SceneGraph.model_validate(data["graph"]), scenario.model_copy(update={"stops": stops})
