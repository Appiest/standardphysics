"""Merging views of a real row of identical things standing side by side.

Four bins stand in a row in Moffitt Library. The detector drew each as its own
rectangle in the same photo, and each rectangle carved to a box the size of one
bin. They touch, so their boxes overlap, and joining on overlap alone folded
the row into two boxes of a bin and a half each, which the workspace then
showed as one thing.

The candidates here are the real carved boxes from that corner of the scan,
recorded in `datasets/replays/moffitt-bins`.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest
from standardphysics_pipeline.discovery.carve import fit_box
from standardphysics_pipeline.discovery.detect import Detection
from standardphysics_pipeline.discovery.merge import Candidate, DiscoveredObject, merge_candidates

CAPTURE = pathlib.Path(__file__).resolve().parents[3] / "datasets" / "replays" / "moffitt-bins"
ONE_BIN_AT_MOST = 0.75
"""Thirty inches. Each bin carves to between 20 and 27 inches along its longer side."""


@pytest.fixture(scope="module")
def candidates() -> list[Candidate]:
    detections = json.loads((CAPTURE / "candidates.json").read_text())
    points = np.load(CAPTURE / "candidate-points.npz")
    return [
        Candidate(
            Detection(item["frame_id"], item["name"], tuple(item["box"]), item["movable"], item["confidence"]),
            fit_box(points[f"points_{index:02d}"]),
        )
        for index, item in enumerate(detections)
    ]


@pytest.fixture(scope="module")
def merged(candidates) -> list[DiscoveredObject]:
    return merge_candidates(candidates)


def holding(objects: list[DiscoveredObject], candidate: Candidate) -> DiscoveredObject:
    """Of the merged objects that took a view from this frame, the one centred nearest this candidate."""
    centre = np.asarray(candidate.box.centre)
    return min(
        (one for one in objects if candidate.detection.frame_id in one.frame_ids),
        key=lambda one: float(np.linalg.norm(np.asarray(one.box.centre) - centre)),
    )


def bins_in(candidates: list[Candidate], frame_id: str) -> list[Candidate]:
    return [one for one in candidates if one.detection.frame_id == frame_id and one.detection.name == "bin"]


class TestARowOfBins:
    def test_four_bins_drawn_in_one_photo_stay_four_objects(self, candidates, merged):
        drawn = bins_in(candidates, "frame-30039")
        assert len(drawn) == 4
        assert len({id(holding(merged, one)) for one in drawn}) == 4

    def test_no_bin_is_merged_into_something_wider_than_one_bin(self, candidates, merged):
        for one in bins_in(candidates, "frame-30039"):
            length, width, _ = holding(merged, one).box.dimensions
            assert max(length, width) <= ONE_BIN_AT_MOST

    def test_a_bin_is_called_a_bin(self, candidates, merged):
        assert {holding(merged, one).name for one in bins_in(candidates, "frame-30039")} == {"bin"}


class TestTheBenchBesideThem:
    def test_a_bench_seen_from_five_places_is_still_one_bench(self, candidates, merged):
        big_views = [one for one in candidates if one.detection.name == "bench" and len(one.box.points) > 1000]
        assert len(big_views) == 2
        assert len({id(holding(merged, one)) for one in big_views}) == 1
        assert holding(merged, big_views[0]).views == 5
