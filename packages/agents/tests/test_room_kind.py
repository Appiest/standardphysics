"""Whether a scan is a shop, a home, or neither, decided from what is in it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import standardphysics_fixtures
from standardphysics_agents.checks import roles
from standardphysics_agents.router import state_for
from standardphysics_fixtures import build_graph
from standardphysics_pipeline import parse_room_json, reconstruct

BEDROOM = Path(standardphysics_fixtures.__file__).parent / "data/real/apple_bedroom3.room.json"


@pytest.fixture(scope="module")
def bedroom():
    return reconstruct(parse_room_json(json.loads(BEDROOM.read_text())))


def _relabelled(graph, category: str, label: str, labeled_by: str):
    return graph.model_copy(update={"nodes": [
        node.model_copy(update={"label": label, "labeled_by": labeled_by})
        if node.raw_category == category else node
        for node in graph.nodes
    ]})


def _without(graph, category: str):
    return graph.model_copy(
        update={"nodes": [node for node in graph.nodes if node.raw_category != category]}
    )


def test_the_boba_shop_is_a_service_business():
    assert roles.room_kind(build_graph()) == "service"


def test_a_scanned_bedroom_is_a_home(bedroom):
    assert roles.room_kind(bedroom) == "home"


def test_the_same_room_without_its_bed_is_a_general_room(bedroom):
    assert roles.room_kind(_without(bedroom, "bed")) == "general"


def test_a_counter_astra_named_in_a_bedroom_is_not_somewhere_people_order(bedroom):
    labelled = _relabelled(bedroom, "table", "Counter", "astra")
    assert roles.service_counters(labelled) == []
    assert roles.room_kind(labelled) == "home"


def test_a_counter_the_owner_marked_in_a_bedroom_still_counts(bedroom):
    marked = _relabelled(bedroom, "table", "Counter", "owner")
    assert [node.label for node in roles.service_counters(marked)] == ["Counter"]
    assert roles.room_kind(marked) == "service"


def test_the_router_hears_what_kind_of_room_it_is(bedroom, pack):
    assert state_for([], bedroom, pack).summary()["room_kind"] == "home"
    assert state_for([], build_graph(), pack).summary()["room_kind"] == "service"
