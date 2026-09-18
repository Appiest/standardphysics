"""Telling the room from what is in it, by shape rather than by name.

Checked against every room really scanned on this machine. A graph written here
to make the rule fire would contain exactly the distinction it was written to
show, which proves nothing about a room nobody arranged.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from standardphysics_pipeline.ingest import parse_room_json

ROOT = pathlib.Path(__file__).resolve().parents[3]

ENCLOSING = {"wall", "floor", "ceiling", "door", "window", "opening"}
"""What a scanner happens to call the parts of a room.

Used only to check the geometry agrees with it, never to do the telling. The
point is that `bounds_the_room` reaches the same answer without being told.
"""


def _rooms():
    paths = sorted(ROOT.glob("datasets/phone/*/room.json")) + sorted(
        ROOT.glob("services/api/var/scans/*/artifacts/room-json")
    )
    return [(path, parse_room_json(json.loads(path.read_text()))) for path in paths]


@pytest.fixture(scope="module")
def rooms():
    found = [(path, graph) for path, graph in _rooms() if graph.nodes]
    if not found:
        pytest.skip("no scanned room on this machine")
    return found


def test_every_room_has_something_enclosing_it(rooms):
    for path, graph in rooms:
        assert any(graph.bounds_the_room(node) for node in graph.nodes), path.parent.name


def test_every_room_has_something_standing_in_it(rooms):
    for path, graph in rooms:
        assert graph.contents(), path.parent.name


def test_the_shape_agrees_with_what_the_scanner_called_it(rooms):
    """The geometry reaches the scanner's own answer without reading its labels.

    That agreement is what lets the name be dropped: when it holds on every room
    measured, asking what kind of thing something is buys nothing that measuring
    it does not.
    """
    for path, graph in rooms:
        for node in graph.nodes:
            encloses = graph.bounds_the_room(node)
            assert encloses == (node.raw_category in ENCLOSING), (
                f"{path.parent.name}: {node.raw_category} "
                f"{'was' if encloses else 'was not'} read as part of the room"
            )


def test_the_two_halves_never_overlap(rooms):
    for _path, graph in rooms:
        inside = {node.id for node in graph.contents()}
        enclosing = {node.id for node in graph.nodes if graph.bounds_the_room(node)}
        assert not inside & enclosing
        assert len(inside) + len(enclosing) == len(graph.nodes)


def test_how_far_up_a_region_reaches_is_read_through_its_own_turning(rooms):
    """A floor's extents include no height at all, and a wall's include a storey.

    Both come back as a span in the room's frame rather than as whichever extent
    happens to be listed third.
    """
    for _path, graph in rooms:
        for node in graph.nodes:
            reach = graph.upright_extent(node)
            assert reach >= 0
            assert reach <= max(node.dimensions.as_tuple()) + 1e-6
