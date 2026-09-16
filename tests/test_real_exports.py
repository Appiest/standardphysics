"""Real RoomPlan exports from Apple's WWDC23 sample, not files we wrote.

They settle what the synthetic shop cannot: keyed-object enums, column-major
transforms, `parentIdentifier` on doors, a binary plist mapping file, and a
floor that sits wherever the phone started rather than at z = 0.
"""

import json
import pathlib
import plistlib

import pytest
from standardphysics_pipeline.ingest import parse_room_json

REAL = pathlib.Path(__file__).parents[1] / "packages/fixtures/standardphysics_fixtures/data/real"
ROOMS = ["apple_bedroom3", "apple_livingroom"]


def _export(room: str) -> dict:
    return json.loads((REAL / f"{room}.room.json").read_text())


@pytest.mark.parametrize("room", ROOMS)
def test_a_real_export_parses(room):
    graph = parse_room_json(_export(room))
    assert {"wall", "door", "floor", "object"} <= {node.kind for node in graph.nodes}


@pytest.mark.parametrize("room", ROOMS)
def test_the_mapping_file_is_a_plist_naming_every_node(room):
    mapping = plistlib.loads((REAL / f"{room}.metadata.plist").read_bytes())
    graph = parse_room_json(_export(room))
    mapped = {value.upper() for value in mapping.values()}
    assert {str(node.id).upper() for node in graph.nodes} <= mapped


@pytest.mark.parametrize("room", ROOMS)
def test_every_door_names_the_wall_it_sits_in(room):
    payload = _export(room)
    walls = {wall["identifier"] for wall in payload["walls"]}
    assert payload["doors"]
    assert all(door["parentIdentifier"] in walls for door in payload["doors"])
