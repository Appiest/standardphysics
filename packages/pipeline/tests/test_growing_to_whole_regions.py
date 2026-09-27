"""Discovered pieces grown to the whole thing, on two real corners of Moffitt Library.

The service desk at the entrance is four metres long and was named once, by a
rectangle that ran off the edge of the photo, so discovery carved a slice of
one end and then dropped it for being seen from one place. The low wooden
platform in the middle of the floor was named bench, platform and table from
different sides and came back as five overlapping boxes.

The points and rectangles here are the real ones from those two walks,
recorded in `datasets/replays/moffitt-regions`.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest
from standardphysics_contracts import SceneGraph, SceneNode
from standardphysics_pipeline.discovery.carve import MAX_EXTENT, fit_box
from standardphysics_pipeline.discovery.detect import Detection
from standardphysics_pipeline.discovery.discover import _worth_keeping
from standardphysics_pipeline.discovery.grow import grown, regions_of
from standardphysics_pipeline.discovery.merge import Candidate, merge_candidates

CAPTURE = pathlib.Path(__file__).resolve().parents[3] / "datasets" / "replays" / "moffitt-regions"
INCH = 0.0254


def corner(name: str):
    recorded = json.loads((CAPTURE / f"{name}.json").read_text())
    arrays = np.load(CAPTURE / f"{name}-points.npz")
    candidates = [
        Candidate(
            Detection(item["frame_id"], item["name"], tuple(item["box"]), item["movable"], item["confidence"]),
            fit_box(arrays[f"points_{index:03d}"].astype(np.float64)),
        )
        for index, item in enumerate(recorded["candidates"])
    ]
    graph = SceneGraph(scan_id="00000000-0000-0000-0000-000000000000", nodes=[SceneNode.model_validate(node) for node in recorded["sheets"]])
    regions = regions_of(arrays["unclaimed"].astype(np.float64), graph)
    return graph, regions, grown(merge_candidates(candidates), regions)


@pytest.fixture(scope="module")
def entrance():
    return corner("entrance")


@pytest.fixture(scope="module")
def platform():
    return corner("platform")


def named(results, name: str):
    return [(object_, grew) for object_, grew in results if object_.name == name]


def longest(object_) -> float:
    return max(object_.box.dimensions[:2])


class TestTheServiceDesk:
    def test_a_slice_named_once_grows_to_the_whole_desk(self, entrance):
        _, _, results = entrance
        desks = [object_ for object_, grew in named(results, "counter") if grew]
        assert len(desks) == 1
        assert longest(desks[0]) >= 140 * INCH

    def test_the_grown_desk_reaches_the_floor(self, entrance):
        _, regions, results = entrance
        desk = next(object_ for object_, grew in named(results, "counter") if grew)
        assert desk.box.floor_clearance - regions.floor <= 0.02

    def test_a_desk_seen_from_one_place_is_kept_once_it_is_whole(self, entrance):
        graph, _, results = entrance
        desk = next(object_ for object_, grew in named(results, "counter") if grew)
        assert _worth_keeping(desk, graph, 1, grew=True)
        assert not _worth_keeping(desk, graph, 1, grew=False)

    def test_three_gates_side_by_side_stay_three_gates(self, entrance):
        _, _, results = entrance
        gates = [object_ for object_, _ in named(results, "security gate")]
        assert len(gates) == 3
        assert all(longest(gate) <= 40 * INCH for gate in gates)


class TestThePlatform:
    def test_pieces_called_by_different_names_become_one_platform(self, platform):
        _, _, results = platform
        middle = np.array([-5.9, 0.3])
        on_it = [
            object_ for object_, _ in results
            if np.linalg.norm(np.asarray(object_.box.centre[:2]) - middle) < 1.0 and object_.box.volume > 0.05
        ]
        assert len(on_it) == 1
        assert longest(on_it[0]) >= 130 * INCH


class TestNothingGrowsPastFurniture:
    def test_no_grown_box_is_larger_than_any_furniture(self, entrance, platform):
        for _, _, results in (entrance, platform):
            assert all(longest(object_) <= MAX_EXTENT for object_, grew in results if grew)
