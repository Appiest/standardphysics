"""Running the steps a plan is made of, against rooms that were really scanned.

No plan here comes from a model and no room here was built: the steps are written
out so the arithmetic can be checked by hand, and the regions they run over came
off a phone.
"""

from __future__ import annotations

import json
import math
import pathlib
from importlib import import_module

import pytest
from standardphysics_contracts import measured_as
from standardphysics_pipeline.ingest import parse_room_json

compose = import_module("standardphysics_agents.ask.compose")

ROOT = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def graph():
    paths = sorted(ROOT.glob("datasets/phone/*/room.json"))
    if not paths:
        pytest.skip("no scanned room on this machine")
    return parse_room_json(json.loads(paths[0].read_text()))


def _run(graph, steps, say, asked=""):
    return compose._run({"steps": steps, "say": say}, graph, asked)


def handle(graph, node) -> str:
    """What a plan calls this region: a short one, not the scan's own identifier."""
    return compose._handle(graph.nodes.index(node))


class TestNearestMeansNearest:
    """`pick` takes max and min, so a plan says min here and means the closest.

    Reading anything but one particular word as its opposite had "what is closest
    to this" answering with the region on the far side of the room.
    """

    def _spans_by_hand(self, graph, anchor) -> list[float]:
        """Every straight-line centre-to-centre distance from the anchor, nearest first.

        Distances rather than names: several regions in a real room are called
        Wall, so a test that checks which name came back can pass on the wrong
        one."""
        here = anchor.transform.position.as_tuple()
        return sorted(
            math.dist(node.transform.position.as_tuple(), here)
            for node in graph.nodes
            if node.id != anchor.id
        )

    def test_min_finds_the_closest_region(self, graph):
        anchor = graph.nodes[0]
        steps = [
            {"id": "all", "op": "every"},
            {"id": "target", "op": "regions", "ids": [handle(graph, anchor)]},
            {"id": "rest", "op": "without", "of": "all", "other": "target"},
            {"id": "near", "op": "nearest", "of": "rest", "other": "target", "how": "min"},
            {"id": "span", "op": "arith", "of": "near", "other": "target", "how": "gap"},
        ]
        said = _run(graph, steps, "{near} is nearest, at {span}.")
        assert said.figures["span"] == pytest.approx(self._spans_by_hand(graph, anchor)[0])

    def test_max_finds_the_furthest(self, graph):
        anchor = graph.nodes[0]
        steps = [
            {"id": "all", "op": "every"},
            {"id": "target", "op": "regions", "ids": [handle(graph, anchor)]},
            {"id": "rest", "op": "without", "of": "all", "other": "target"},
            {"id": "far", "op": "nearest", "of": "rest", "other": "target", "how": "max"},
            {"id": "span", "op": "arith", "of": "far", "other": "target", "how": "gap"},
        ]
        said = _run(graph, steps, "{far} is furthest, at {span}.")
        assert said.figures["span"] == pytest.approx(self._spans_by_hand(graph, anchor)[-1])


class TestTheAskersNumberAndNobodyElses:
    def test_a_quantity_the_question_stated_is_allowed(self, graph):
        steps = [{"id": "gap", "op": "given", "value": 0.2}]
        said = _run(graph, steps, "Allow {gap}.", asked="leave 20 centimetres between them")
        assert "7.9 inches" in said.text

    def test_a_quantity_written_out_in_words_is_allowed(self, graph):
        """"Less than a metre tall" states a number as plainly as "less than 1 m".

        Reading only digits refused the plan and dropped the question to whatever
        could answer it without one, which answered nought."""
        steps = [{"id": "tall", "op": "given", "value": 1.0}]
        said = _run(graph, steps, "Under {tall}.", asked="less than a metre tall")
        assert "feet" in said.text or "inches" in said.text

    def test_half_a_metre_is_half_a_metre(self, graph):
        steps = [{"id": "gap", "op": "given", "value": 0.5}]
        said = _run(graph, steps, "{gap}.", asked="half a metre wide")
        assert said.figures["gap"] == 0.5

    def test_a_quantity_the_question_never_mentioned_is_refused(self, graph):
        steps = [{"id": "made_up", "op": "given", "value": 2.925}]
        with pytest.raises(compose.CannotCompose):
            _run(graph, steps, "It is {made_up}.", asked="how tall are the walls")


class TestNothingReachesThePageUnworkedOut:
    def test_a_hole_no_step_filled_makes_the_plan_unusable(self, graph):
        with pytest.raises(compose.CannotCompose):
            _run(graph, [{"id": "all", "op": "every"}], "It is {nowhere}.")

    def test_a_sentence_with_no_figures_is_not_an_answer(self, graph):
        with pytest.raises(compose.CannotCompose):
            _run(graph, [{"id": "all", "op": "every"}], "There are some things here.")

    def test_a_step_nothing_implements_is_refused(self, graph):
        with pytest.raises(compose.CannotCompose):
            _run(graph, [{"id": "x", "op": "teleport"}], "{x}")


class TestWhatThePlannerSeesIsWhatTheEngineRuns:
    """The two copies of a region's measurements drifted apart once.

    The planner was shown a floor nine metres tall while the engine measured it
    as flat, so every area worked out from the planner's reading came back as
    nothing."""

    def test_every_figure_shown_is_the_figure_evaluated(self, graph):
        for view, node in zip(compose._regions(graph), graph.nodes):
            for name in compose.FIELDS:
                assert view[name] == pytest.approx(compose._field(node, name), abs=1e-3)

    def test_a_floor_has_an_area_and_no_height(self, graph):
        biggest = max(graph.nodes, key=lambda node: compose._field(node, "footprint_m2"))
        assert compose._field(biggest, "footprint_m2") > 1.0
        assert compose._field(biggest, "height_m") == pytest.approx(0.0, abs=0.05)


class TestNarrowingASet:
    """`where` used to read an operator nobody implemented as greater-than."""

    def test_an_operator_nothing_implements_is_refused(self, graph):
        steps = [
            {"id": "all", "op": "every"},
            {"id": "some", "op": "where", "of": "all", "field": "width_m",
             "how": "roughly", "value": 1.0},
        ]
        with pytest.raises(compose.CannotCompose):
            _run(graph, steps, "{some}", asked="about 1 metre")

    def test_equal_finds_a_region_matching_itself(self, graph):
        node = graph.nodes[0]
        steps = [
            {"id": "all", "op": "every"},
            {"id": "one", "op": "regions", "ids": [handle(graph, node)]},
            {"id": "w", "op": "value", "of": "one", "field": "width_m"},
            {"id": "same", "op": "where", "of": "all", "field": "width_m",
             "how": "eq", "other": "w"},
            {"id": "n", "op": "count", "of": "same"},
        ]
        said = _run(graph, steps, "{n}.")
        assert int(float(said.text.rstrip("."))) >= 1

    def test_a_threshold_from_nowhere_is_refused(self, graph):
        steps = [
            {"id": "all", "op": "every"},
            {"id": "some", "op": "where", "of": "all", "field": "width_m",
             "how": "gt", "value": 2.925},
        ]
        with pytest.raises(compose.CannotCompose):
            _run(graph, steps, "{some}", asked="which ones are wide")


class TestArithmetic:
    def test_counting_every_region_agrees_with_the_scan(self, graph):
        steps = [{"id": "all", "op": "every"}, {"id": "n", "op": "count", "of": "all"}]
        said = _run(graph, steps, "{n} regions.")
        assert said.text == f"{len(graph.nodes)} regions."

    def test_two_lengths_multiply_into_an_area(self, graph):
        node = max(graph.nodes, key=lambda n: measured_as(n).x * measured_as(n).z)
        steps = [
            {"id": "one", "op": "regions", "ids": [handle(graph, node)]},
            {"id": "w", "op": "value", "of": "one", "field": "width_m"},
            {"id": "h", "op": "value", "of": "one", "field": "height_m"},
            {"id": "area", "op": "arith", "of": "w", "other": "h", "how": "mul"},
        ]
        said = _run(graph, steps, "{area}.")
        size = measured_as(node)
        assert said.text == f"{size.x * size.z:.1f} square metres."

    def test_a_centimetre_apart_is_not_the_same(self, graph):
        """A phone can tell a centimetre, so it should not call that equal.

        The threshold was two per cent, which on a pair of two-metre shelves is
        four centimetres, and reported one a centimetre and a half taller than
        another as being the same height."""
        assert compose._compare((1.959, compose.METRES), (1.944, compose.METRES)) == 1.0
        assert compose._compare((1.9590, compose.METRES), (1.9585, compose.METRES)) == 0.0

    def test_the_same_says_so_when_the_scan_did_measure_a_difference(self, graph):
        """Two tables 2 mm apart are the same to a phone, and the answer admits it."""
        found = compose._verdict((0.813, compose.METRES), (0.811, compose.METRES))
        said = compose._whichever(found, "?taller than|the same height as|shorter than")
        assert said.startswith("the same height as")
        assert "within a centimetre" in said

    def test_the_same_says_nothing_more_when_they_are_identical(self, graph):
        found = compose._verdict((0.811, compose.METRES), (0.811, compose.METRES))
        said = compose._whichever(found, "?taller than|the same height as|shorter than")
        assert said == "the same height as"

    def test_a_straight_line_includes_the_rise_and_the_floor_does_not(self, graph):
        """Centre to centre was measured across the floor only, which misplaced
        things whose middles sit at different heights."""
        tall = max(graph.nodes, key=lambda n: n.transform.position.z)
        low = min(graph.nodes, key=lambda n: n.transform.position.z)
        rise = tall.transform.position.z - low.transform.position.z
        across = compose._gap([tall], [low], across_floor=True)
        straight = compose._gap([tall], [low])
        assert straight == pytest.approx((across**2 + rise**2) ** 0.5)
        assert straight >= across

    def test_a_comparison_says_whichever_of_three_holds(self, graph):
        node = max(graph.nodes, key=lambda n: measured_as(n).x)
        steps = [
            {"id": "one", "op": "regions", "ids": [handle(graph, node)]},
            {"id": "w", "op": "value", "of": "one", "field": "width_m"},
            {"id": "h", "op": "value", "of": "one", "field": "height_m"},
            {"id": "v", "op": "compare", "of": "w", "other": "h"},
        ]
        said = _run(graph, steps, "It is {v?wider|square|taller}.")
        size = measured_as(node)
        wider = size.x > size.z
        assert said.text == ("It is wider." if wider else "It is taller.")
