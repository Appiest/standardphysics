"""Tier 2 and tier 3, and the guard against a rule with nothing behind it."""

from __future__ import annotations

import pytest
from standardphysics_agents import assess
from standardphysics_agents.checks import COVERED, door_verdict
from standardphysics_agents.checks.dining import (
    required_count,
    surface_height_inches,
    within_range,
)
from standardphysics_agents.checks.protrusions import (
    in_the_hazard_band,
    is_mounted,
    leading_edge_inches,
    projection_inches,
)
from standardphysics_agents.checks.rectangles import clear_floor, intruders, rectangle
from standardphysics_agents.evaluation import variants as v
from standardphysics_agents.router import state_for
from standardphysics_contracts import Mat4, Vec3, to_inches, to_meters
from standardphysics_fixtures.shop import node_id

WEST_WALL = node_id("wall_west")
TABLE_3 = node_id("table_3")


def _findings(graph, scenario, measure, pack, ledger, check_id):
    result = assess(
        graph, scenario, measure, rules=pack, ledger=ledger, max_tier=3
    )
    return [f for f in result.findings if f.check_id == check_id]


def _shelf(name, centre, dims):
    return v.box(name, "Shelf", centre, dims, movable=False)


def test_every_rule_in_the_pack_has_a_check_or_is_a_request(pack):
    """A rule with nothing behind it produces no findings, which from the
    outside is indistinguishable from a shop that passes it."""
    for rule in pack.within_tier(3):
        assert rule.id in COVERED, rule.id


def test_a_verified_rule_with_no_check_reports_itself(graph, scenario, pipeline, pack):
    from standardphysics_agents import VerificationLedger
    from standardphysics_agents.checks import CheckContext, run_checks

    invented = pack.by_id("route_clear_width").model_copy(
        update={"id": "a_rule_nobody_implemented"}
    )
    widened = pack.model_copy(update={"rules": [*pack.rules, invented]})
    ledger = VerificationLedger().record(invented, verified_by="test")
    result = run_checks(
        CheckContext(
            graph=graph, scenario=scenario, measure=pipeline,
            rules=widened, ledger=ledger,
        )
    )
    gaps = {gap.rule_id: gap.waiting_on for gap in result.unevaluated}
    assert "a check in packages/agents" in gaps["a_rule_nobody_implemented"]


class TestClearFloorPatches:
    def test_a_patch_is_the_size_asked_for(self):
        space = rectangle(Vec3(x=0.0, y=0.0, z=0.0), 2.0, 1.0)
        assert max(x for x, _ in space) - min(x for x, _ in space) == pytest.approx(2.0)
        assert max(y for _, y in space) - min(y for _, y in space) == pytest.approx(1.0)

    def test_a_turned_patch_turns(self):
        space = rectangle(Vec3(x=0.0, y=0.0, z=0.0), 2.0, 1.0, rotation=(0.0, 1.0))
        assert max(x for x, _ in space) - min(x for x, _ in space) == pytest.approx(1.0)

    def test_it_finds_what_is_standing_in_it(self, graph):
        counter = graph.by_id(node_id("counter"))
        space = rectangle(counter.transform.position, 1.0, 1.0)
        assert counter.id in intruders(graph, space)

    def test_it_can_be_told_to_ignore_something(self, graph):
        counter = graph.by_id(node_id("counter"))
        space = rectangle(counter.transform.position, 1.0, 1.0)
        assert counter.id not in intruders(
            graph, space, ignoring=frozenset({counter.id})
        )

    def test_touching_an_edge_is_not_standing_in_it(self, graph):
        """A patch in front of a door starts at the wall the door sits in."""
        middle = Vec3(x=0.0, y=0.0, z=0.0)
        assert clear_floor(graph, middle, 12.0, 12.0).fits

    def test_the_reported_size_is_the_size_asked_for(self, graph):
        space = clear_floor(graph, Vec3(x=0.0, y=0.0, z=0.0), 48.0, 30.0)
        assert (space.inches_wide, space.inches_deep) == (48.0, 30.0)


class TestDoorManeuveringClearance:
    """ADA 2010 404.2.4, read from the stricter end of table 404.2.4.1."""

    def test_deep_enough_to_pull_complies_either_way(self):
        assert door_verdict(pull_fits=True, push_fits=True) == "complies_either_way"

    def test_deep_enough_to_push_only_depends_on_the_swing(self):
        assert door_verdict(pull_fits=False, push_fits=True) == "depends_on_the_swing"

    def test_too_tight_to_push_fails_either_way(self):
        assert door_verdict(pull_fits=False, push_fits=False) == "too_tight"

    def test_an_open_doorway_passes(self, graph, scenario, pipeline, pack, ledger):
        found = _findings(
            graph, scenario, pipeline, pack, ledger, "door_maneuvering_clearance"
        )
        assert len(found) == 1
        assert found[0].outcome == "passes"

    def test_the_wall_the_door_sits_in_is_not_counted_against_it(
        self, graph, scenario, pipeline, pack, ledger
    ):
        """Measuring from the middle of a doorway puts half a wall inside the
        clearance being measured."""
        found = _findings(
            graph, scenario, pipeline, pack, ledger, "door_maneuvering_clearance"
        )
        assert found[0].outcome != "problem"

    def test_a_chair_in_the_doorway_fails(
        self, graph, scenario, pipeline, pack, ledger
    ):
        blocked = v.add(
            graph, v.box("blocker", "Chair", (0.0, -3.3, 0.45), (0.45, 0.45, 0.9))
        )
        found = _findings(
            blocked, scenario, pipeline, pack, ledger, "door_maneuvering_clearance"
        )
        assert found[0].outcome == "problem"
        assert "not enough room to open" in found[0].title

    def test_it_says_what_to_do(self, graph, scenario, pipeline, pack, ledger):
        blocked = v.add(
            graph, v.box("blocker", "Chair", (0.0, -3.3, 0.45), (0.45, 0.45, 0.9))
        )
        found = _findings(
            blocked, scenario, pipeline, pack, ledger, "door_maneuvering_clearance"
        )
        assert found[0].fix.startswith("Keep the floor")


class TestProtrudingObjects:
    """ADA 2010 307: what you walk into, not what you walk around."""

    def test_the_leading_edge_is_the_bottom(self, graph):
        counter = graph.by_id(node_id("counter"))
        bottom = counter.transform.position.z - counter.dimensions.z / 2
        assert leading_edge_inches(counter) == pytest.approx(to_inches(bottom))

    def test_something_standing_on_the_floor_is_not_mounted(self, graph):
        assert not is_mounted(graph.by_id(TABLE_3))

    def test_something_up_on_a_wall_is(self):
        assert is_mounted(_shelf("s", (-2.8, 1.5, 1.1), (0.3, 1.2, 0.3)))

    def test_a_shelf_at_head_height_is_in_the_band(self, pack):
        rule = pack.by_id("protruding_objects")
        assert in_the_hazard_band(_shelf("s", (-2.8, 1.5, 1.1), (0.3, 1.2, 0.3)), rule)

    def test_something_a_cane_would_find_is_not(self, pack):
        """Below 27 inches a cane sweeping the floor finds it first."""
        rule = pack.by_id("protruding_objects")
        assert not in_the_hazard_band(
            _shelf("s", (-2.8, 1.5, 0.5), (0.3, 1.2, 0.3)), rule
        )

    def test_the_band_is_exclusive_above_27(self, pack):
        """307.2 reads 'more than 27 inches', so exactly 27 is outside."""
        rule = pack.by_id("protruding_objects")
        edge = to_meters(27.0) + (0.3 / 2)
        assert not in_the_hazard_band(
            _shelf("s", (-2.8, 1.5, edge), (0.3, 1.2, 0.3)), rule
        )

    def test_the_band_is_inclusive_at_80(self, pack):
        """307.2 reads 'not more than 80 inches', so exactly 80 is inside."""
        rule = pack.by_id("protruding_objects")
        edge = to_meters(80.0) + (0.3 / 2)
        assert in_the_hazard_band(
            _shelf("s", (-2.8, 1.5, edge), (0.3, 1.2, 0.3)), rule
        )

    def test_projection_is_measured_from_the_wall_face(self, graph):
        wall = graph.by_id(WEST_WALL)
        shelf = _shelf("s", (-2.8, 1.5, 1.1), (0.3, 1.2, 0.3))
        centre = Vec3(x=0.0, y=0.0, z=0.0)
        assert projection_inches(shelf, wall, centre) == pytest.approx(11.8, abs=0.1)

    def test_a_shelf_right_at_the_four_inch_limit_passes(
        self, graph, scenario, pipeline, pack, ledger
    ):
        """The limit is inclusive: 4 inches exactly meets 307.2."""
        rule = pack.by_id("protruding_objects")
        depth = rule.parameter("wall_mounted_max_projection_inches") * 0.0254
        centre_x = -3.0 + 0.1 / 2 + depth / 2
        at_limit = v.add(
            graph, _shelf("wall_shelf", (centre_x, 1.5, 1.1), (depth, 1.2, 0.3))
        )
        found = _findings(
            at_limit, scenario, pipeline, pack, ledger, "protruding_objects"
        )
        assert found[0].outcome == "passes"
        assert found[0].measured_inches == pytest.approx(4.0, abs=0.1)

    def test_a_shelf_a_hair_past_the_limit_is_a_problem(
        self, graph, scenario, pipeline, pack, ledger
    ):
        depth = 0.11
        centre_x = -3.0 + 0.1 / 2 + depth / 2
        over = v.add(graph, _shelf("wall_shelf", (centre_x, 1.5, 1.1), (depth, 1.2, 0.3)))
        found = _findings(over, scenario, pipeline, pack, ledger, "protruding_objects")
        assert found[0].outcome == "problem"

    def test_a_deep_shelf_at_head_height_is_a_problem(
        self, graph, scenario, pipeline, pack, ledger
    ):
        hazard = v.add(graph, _shelf("wall_shelf", (-2.8, 1.5, 1.1), (0.3, 1.2, 0.3)))
        found = _findings(
            hazard, scenario, pipeline, pack, ledger, "protruding_objects"
        )
        assert len(found) == 1
        assert found[0].outcome == "problem"
        assert found[0].measured_inches == pytest.approx(11.8, abs=0.1)

    def test_a_shallow_one_passes(self, graph, scenario, pipeline, pack, ledger):
        tucked = v.add(
            graph, _shelf("wall_shelf", (-2.9119, 1.5, 1.1), (0.0762, 1.2, 0.3))
        )
        found = _findings(
            tucked, scenario, pipeline, pack, ledger, "protruding_objects"
        )
        assert found[0].outcome == "passes"

    def test_a_wall_with_nothing_on_it_reports_nothing(
        self, graph, scenario, pipeline, pack, ledger
    ):
        """There is nothing there to report."""
        assert not _findings(
            graph, scenario, pipeline, pack, ledger, "protruding_objects"
        )


class TestDiningSurfaces:
    """ADA 2010 902.3 for the height and 226.1 for the share."""

    def test_the_height_is_the_top_of_the_table(self, graph):
        table = graph.by_id(TABLE_3)
        top = table.transform.position.z + table.dimensions.z / 2
        assert surface_height_inches(table) == pytest.approx(to_inches(top))

    def test_the_range_is_twenty_eight_to_thirty_four(self, pack):
        rule = pack.by_id("dining_surface_height")
        assert within_range(28.0, rule)
        assert within_range(34.0, rule)
        assert not within_range(27.9, rule)
        assert not within_range(42.0, rule)

    def test_one_in_twenty_and_never_fewer_than_one(self, pack):
        rule = pack.by_id("dining_surface_height")
        assert required_count(4, rule) == 1
        assert required_count(40, rule) == 2
        assert required_count(0, rule) == 0

    def test_the_fixture_tables_are_a_usable_height(
        self, graph, scenario, pipeline, pack, ledger
    ):
        found = _findings(
            graph, scenario, pipeline, pack, ledger, "dining_surface_height"
        )
        assert len(found) == 1
        assert found[0].outcome == "passes"

    def test_bar_height_tables_fail(self, graph, scenario, pipeline, pack, ledger):
        height = to_meters(42.0)
        tall = graph
        for name in ("table_1", "table_2", "table_3", "table_4"):
            node = tall.by_id(node_id(name))
            position = node.transform.position
            tall = v.replace(
                tall, node.id,
                dimensions=Vec3(x=node.dimensions.x, y=node.dimensions.y, z=height),
                transform=Mat4.translation(position.x, position.y, height / 2),
            )
        found = _findings(
            tall, scenario, pipeline, pack, ledger, "dining_surface_height"
        )
        assert found[0].outcome == "problem"
        assert "None of the tables" in found[0].title

    def test_a_shop_with_no_seating_reports_nothing(
        self, scenario, pipeline, pack, ledger
    ):
        from standardphysics_fixtures import build_graph

        bare = v.drop(
            build_graph(), *[node_id(f"table_{i}") for i in range(1, 5)]
        )
        assert not _findings(
            bare, scenario, pipeline, pack, ledger, "dining_surface_height"
        )


class TestWhatARearrangementCanFix:
    def test_a_height_is_not_something_moving_furniture_changes(self, pack):
        """No rearrangement makes a counter shorter."""
        assert not pack.by_id("service_counter_height").rearrangeable
        assert not pack.by_id("dining_surface_height").rearrangeable
        assert not pack.by_id("protruding_objects").rearrangeable

    def test_a_clearance_is(self, pack):
        assert pack.by_id("route_clear_width").rearrangeable
        assert pack.by_id("door_maneuvering_clearance").rearrangeable

    def test_the_router_never_sends_a_fix_at_a_table_height(
        self, scenario, pipeline, pack, ledger
    ):
        from standardphysics_fixtures import build_graph

        height = to_meters(42.0)
        tall = build_graph()
        for name in ("table_1", "table_2", "table_3", "table_4"):
            node = tall.by_id(node_id(name))
            position = node.transform.position
            tall = v.replace(
                tall, node.id,
                dimensions=Vec3(x=node.dimensions.x, y=node.dimensions.y, z=height),
                transform=Mat4.translation(position.x, position.y, height / 2),
            )
        result = assess(
            tall, scenario, pipeline, rules=pack, ledger=ledger, max_tier=3
        )
        state = state_for(result.findings, tall, pack)
        heights = {
            f.id for f in result.problems if f.check_id == "dining_surface_height"
        }
        assert heights
        assert not heights & set(state.fixable_finding_ids)

    def test_a_reach_range_question_is_asked_rather_than_measured(self, pack):
        """308 is about switches and card readers, which a scan does not
        identify."""
        assert pack.by_id("reach_range").evidence == "photo"
