"""The fix agent: what it may move, and what it says when nothing works."""

from __future__ import annotations

import pytest
from standardphysics_agents import assess
from standardphysics_agents.fix import (
    apply_moves,
    candidates,
    collision_shape,
    door_keep_clear,
    is_allowed,
    pinch_from,
    propose_fix,
    violations,
)
from standardphysics_contracts import (
    Mat4,
    NodeMove,
    Scenario,
    SceneGraph,
    SceneNode,
    Stop,
    Vec3,
    to_meters,
)
from standardphysics_fixtures.shop import FIX_SHIFT_INCHES, node_id
from standardphysics_pipeline import (
    PipelineMeasurements,
    contains_point,
    floor_polygon,
    footprint,
    gap_between,
    polygon_bounds,
)

CASE_EAST = node_id("case_east")
CASE_WEST = node_id("case_west")
COUNTER = node_id("counter")
DOOR = node_id("door_front")

PINCH_INCHES = 31.0


def _pinch_finding(result):
    return [
        f
        for f in result.problems
        if f.check_id == "route_clear_width"
        and round(f.measured_inches, 1) == PINCH_INCHES
    ]


def _kinds(base, candidate):
    return {item.kind for item in violations(base, candidate)}


@pytest.fixture(scope="session")
def fixed_shop():
    """A room where one fixed shelf pinches the only route.

    Nothing movable causes the problem, so the search has to run out and offer
    the owner something specific instead.
    """
    space = _node("floor", "floor", "Floor", (0.0, 0.0, 0.0), (4.0, 8.0, 0.01), False)
    walls = [
        _node("w_west", "wall", "Wall", (-2.0, 0.0, 1.5), (0.1, 8.0, 3.0), False),
        _node("w_east", "wall", "Wall", (2.0, 0.0, 1.5), (0.1, 8.0, 3.0), False),
        _node("w_south", "wall", "Wall", (0.0, -4.0, 1.5), (4.0, 0.1, 3.0), False),
        _node("w_north", "wall", "Wall", (0.0, 4.0, 1.5), (4.0, 0.1, 3.0), False),
    ]
    inner = to_meters(PINCH_INCHES) / 2
    width = 1.95 - inner
    centre = inner + width / 2
    shelf_west = _node(
        "shelf_west", "object", "Shelf", (-centre, 0.0, 0.5), (width, 0.5, 1.0), False
    )
    shelf_east = _node(
        "shelf_east", "object", "Shelf", (centre, 0.0, 0.5), (width, 0.5, 1.0), False
    )
    graph = SceneGraph(
        scan_id=node_id("fixed_shop"),
        nodes=[space, *walls, shelf_west, shelf_east],
    )
    scenario = Scenario(
        name="Walk through",
        stops=[
            Stop(name="Entrance", position=Vec3(x=0.0, y=-3.5, z=0.0)),
            Stop(name="Counter", position=Vec3(x=0.0, y=3.5, z=0.0)),
        ],
    )
    return graph, scenario


def _node(name, kind, label, centre, dims, movable):
    return SceneNode(
        id=node_id(name),
        kind=kind,
        label=label,
        raw_category=kind,
        dimensions=Vec3(x=dims[0], y=dims[1], z=dims[2]),
        transform=Mat4.translation(*centre),
        movable=movable,
    )


class TestHardConstraints:
    def test_touching_a_wall_is_not_a_collision(self, graph):
        """The shop as built breaks no hard constraint."""
        assert is_allowed(graph, graph)

    def test_the_documented_five_inch_fix_is_a_legal_move(self, graph):
        """It was not, until Lane D left six inches beside each case.

        See docs/handoffs/C-to-D.md. The case used to end flush against the
        wall, so the one move the demo script called for was the one the room
        forbade.
        """
        shifted = apply_moves(
            graph,
            [
                NodeMove(
                    node_id=CASE_EAST,
                    delta_translation=Vec3(
                        x=to_meters(FIX_SHIFT_INCHES), y=0.0, z=0.0
                    ),
                )
            ],
        )
        assert violations(graph, shifted) == []

    def test_a_case_shoved_past_its_wall_is_still_rejected(self, graph):
        past_the_wall = apply_moves(
            graph,
            [
                NodeMove(
                    node_id=CASE_EAST,
                    delta_translation=Vec3(x=to_meters(24.0), y=0.0, z=0.0),
                )
            ],
        )
        assert _kinds(graph, past_the_wall) & {"collided", "left_the_floor"}

    def test_moving_something_fixed_is_rejected(self, graph):
        shoved = apply_moves(
            graph,
            [NodeMove(node_id=COUNTER, delta_translation=Vec3(x=0.0, y=-1.0, z=0.0))],
        )
        assert "moved_something_fixed" in _kinds(graph, shoved)

    def test_leaving_the_floor_is_rejected(self, graph):
        outside = apply_moves(
            graph,
            [NodeMove(node_id=node_id("chair_1"), delta_translation=Vec3(x=0.0, y=20.0, z=0.0))],
        )
        assert "left_the_floor" in _kinds(graph, outside)

    def test_a_real_rotated_roomplan_floor_accepts_inside_moves_and_rejects_its_aabb_corner(self):
        floor = SceneNode(
            id=node_id("test1_floor"), kind="floor", label="Floor", raw_category="floor",
            dimensions=Vec3(x=11.220307, y=0.0, z=10.557267),
            transform=Mat4(m=[
                0.9996333, 0.0, -0.027079854, -3.1393082,
                0.027079882, 0.0, 0.9996333, 0.77995247,
                0.0, -1.0, 0.0, 0.0,
                0.0, 0.0, 0.0, 1.0,
            ]),
        )
        chair = _node("test1_chair", "object", "Chair", (-3.14, 0.78, 0.5), (0.4, 0.4, 1.0), True)
        graph = SceneGraph(scan_id=node_id("test1_room"), nodes=[floor, chair])

        inside = apply_moves(graph, [NodeMove(node_id=chair.id, delta_translation=Vec3(x=0.5, y=0.0, z=0.0))])
        assert "left_the_floor" not in _kinds(graph, inside)

        boundary = floor_polygon(floor)
        min_x, min_y, _, _ = polygon_bounds(boundary)
        aabb_corner = (min_x + 0.02, min_y + 0.02)
        assert not contains_point(boundary, aabb_corner)
        false_pass = apply_moves(graph, [NodeMove(
            node_id=chair.id,
            delta_translation=Vec3(x=aabb_corner[0] - chair.transform.position.x, y=aabb_corner[1] - chair.transform.position.y, z=0.0),
        )])
        assert "left_the_floor" in _kinds(graph, false_pass)

    def test_parking_in_front_of_the_door_is_rejected(self, graph):
        door = graph.by_id(DOOR)
        chair = graph.by_id(node_id("chair_1"))
        target = door.transform.position
        blocking = apply_moves(
            graph,
            [
                NodeMove(
                    node_id=chair.id,
                    delta_translation=Vec3(
                        x=target.x - chair.transform.position.x,
                        y=target.y + 0.5 - chair.transform.position.y,
                        z=0.0,
                    ),
                )
            ],
        )
        assert _kinds(graph, blocking) & {"blocked_a_door", "collided"}

    def test_a_resize_is_rejected(self, graph):
        stretched = graph.model_copy(
            update={
                "nodes": [
                    node.model_copy(
                        update={"dimensions": Vec3(x=0.3, y=0.3, z=0.9)}
                    )
                    if node.id == node_id("chair_1")
                    else node
                    for node in graph.nodes
                ]
            }
        )
        assert "resized" in _kinds(graph, stretched)

    def test_losing_a_chair_is_rejected(self, graph):
        short = graph.model_copy(
            update={"nodes": [n for n in graph.nodes if n.id != node_id("chair_1")]}
        )
        assert "inventory_changed" in _kinds(graph, short)

    def test_the_swing_square_covers_the_floor_in_front_of_the_door(self, graph):
        door = graph.by_id(DOOR)
        keep_clear = door_keep_clear(door)
        assert gap_between(keep_clear, footprint(door)) == 0.0

    def test_the_collision_shape_is_smaller_than_the_real_footprint(self, graph):
        node = graph.by_id(CASE_EAST)
        real = footprint(node)
        test_shape = collision_shape(node)
        assert max(x for x, _ in test_shape) < max(x for x, _ in real)


class TestMoves:
    def test_a_move_never_touches_a_dimension(self, graph):
        moved = apply_moves(
            graph,
            [NodeMove(node_id=CASE_EAST, delta_translation=Vec3(x=0.0, y=0.4, z=0.0))],
        )
        assert moved.by_id(CASE_EAST).dimensions == graph.by_id(CASE_EAST).dimensions

    def test_a_quarter_turn_swaps_which_way_a_long_piece_lies(self, graph):
        turned = apply_moves(
            graph,
            [
                NodeMove(
                    node_id=CASE_EAST,
                    delta_translation=Vec3(x=0.0, y=0.0, z=0.0),
                    delta_rotation_z_degrees=90.0,
                )
            ],
        )
        before = footprint(graph.by_id(CASE_EAST))
        after = footprint(turned.by_id(CASE_EAST))
        width = lambda shape: max(x for x, _ in shape) - min(x for x, _ in shape)
        depth = lambda shape: max(y for _, y in shape) - min(y for _, y in shape)
        assert width(after) == pytest.approx(depth(before))
        assert depth(after) == pytest.approx(width(before))

    def test_the_original_layout_is_left_alone(self, graph):
        original = graph.by_id(CASE_EAST).transform.m.copy()
        apply_moves(
            graph,
            [NodeMove(node_id=CASE_EAST, delta_translation=Vec3(x=1.0, y=0.0, z=0.0))],
        )
        assert graph.by_id(CASE_EAST).transform.m == original

    def test_the_revision_advances_so_a_stale_result_is_visible(self, graph):
        moved = apply_moves(
            graph,
            [NodeMove(node_id=CASE_EAST, delta_translation=Vec3(x=0.0, y=0.1, z=0.0))],
        )
        assert moved.revision == graph.revision + 1


class TestCandidateLadder:
    def test_candidates_come_least_disruptive_first(self, graph, scenario, pipeline, ledger, pack):
        before = assess(graph, scenario, pipeline, rules=pack, ledger=ledger)
        pinch = pinch_from(_pinch_finding(before)[0], graph)
        ladder = candidates(pinch)
        assert ladder
        from standardphysics_agents.fix.strategies import PREFERENCE

        assert ladder == sorted(
            ladder, key=lambda c: (c.disruption, PREFERENCE.index(c.strategy))
        )

    def test_opening_a_gap_from_both_sides_is_tried_first(
        self, graph, scenario, pipeline, ledger, pack
    ):
        before = assess(graph, scenario, pipeline, rules=pack, ledger=ledger)
        pinch = pinch_from(_pinch_finding(before)[0], graph)
        assert candidates(pinch)[0].strategy == "split_the_gap"

    def test_a_pinch_with_nothing_movable_offers_no_candidates(
        self, fixed_shop, pipeline, ledger, pack
    ):
        graph, scenario = fixed_shop
        before = assess(graph, scenario, pipeline, rules=pack, ledger=ledger)
        problems = [f for f in before.problems if f.check_id == "route_clear_width"]
        assert problems
        pinch = pinch_from(problems[0], graph)
        assert not pinch.movable
        assert candidates(pinch) == []


@pytest.fixture(scope="module")
def outcome(graph, scenario, pipeline, ledger, pack):
    """One search against the fixture shop, shared by everything below."""
    before = assess(graph, scenario, pipeline, rules=pack, ledger=ledger)
    return before, propose_fix(
        graph, scenario, pipeline, _pinch_finding(before),
        rules=pack, ledger=ledger, baseline=before,
    )


@pytest.fixture(scope="module")
def exhausted(fixed_shop, ledger, pack):
    """One search against a shop where nothing movable causes the problem."""
    graph, scenario = fixed_shop
    measure = PipelineMeasurements()
    before = assess(graph, scenario, measure, rules=pack, ledger=ledger)
    problems = [f for f in before.problems if f.check_id == "route_clear_width"]
    assert problems, "the fixed shop has to have a pinch to be worth testing"
    return graph, propose_fix(
        graph, scenario, measure, problems,
        rules=pack, ledger=ledger, baseline=before,
    )


class TestProposals:
    def test_the_search_finds_an_arrangement_for_the_fixture_pinch(self, outcome):
        _, fixed = outcome
        assert fixed.found
        assert fixed.measured > 0

    def test_the_arrangement_clears_the_finding(
        self, outcome, scenario, pipeline, ledger, pack
    ):
        _, fixed = outcome
        after = assess(fixed.graph, scenario, pipeline, rules=pack, ledger=ledger)
        assert not _pinch_finding(after)

    def test_the_arrangement_breaks_nothing_that_worked(
        self, outcome, scenario, pipeline, ledger, pack
    ):
        before, fixed = outcome
        after = assess(fixed.graph, scenario, pipeline, rules=pack, ledger=ledger)
        was = {f.id for f in before.problems}
        assert not {f.id for f in after.problems} - was

    def test_every_piece_of_furniture_survives(self, outcome):
        _, fixed = outcome
        assert fixed.proposal.preserves_inventory

    def test_only_movable_things_moved(self, outcome, graph):
        _, fixed = outcome
        for move in fixed.proposal.moves:
            assert graph.by_id(move.node_id).movable

    def test_the_proposal_is_pinned_to_the_layout_it_was_built_against(
        self, outcome, graph
    ):
        from standardphysics_contracts import graph_hash

        _, fixed = outcome
        assert fixed.proposal.base_graph_hash == graph_hash(graph)

    def test_the_rationale_is_one_plain_sentence(self, outcome):
        _, fixed = outcome
        assert fixed.proposal.rationale.endswith(".")
        assert "node" not in fixed.proposal.rationale.casefold()

    def test_the_same_layout_proposes_the_same_thing_twice(
        self, graph, scenario, pipeline, ledger, pack
    ):
        before = assess(graph, scenario, pipeline, rules=pack, ledger=ledger)
        first = propose_fix(
            graph, scenario, pipeline, _pinch_finding(before),
            rules=pack, ledger=ledger, baseline=before,
        )
        second = propose_fix(
            graph, scenario, pipeline, _pinch_finding(before),
            rules=pack, ledger=ledger, baseline=before,
        )
        assert first.proposal.id == second.proposal.id

    def test_an_external_workflow_regression_rejects_the_candidate(
        self, graph, scenario, pipeline, ledger, pack
    ):
        before = assess(graph, scenario, pipeline, rules=pack, ledger=ledger)
        result = propose_fix(
            graph,
            scenario,
            pipeline,
            _pinch_finding(before),
            rules=pack,
            ledger=ledger,
            baseline=before,
            limit=1,
            offer_relaxation=False,
            candidate_rejection=lambda base, candidate: "workflow_regression",
        )

        assert result.found is False
        assert "workflow_regression" in result.rejected


class TestExhaustion:
    def test_nothing_is_proposed(self, exhausted):
        assert not exhausted[1].found

    def test_the_message_is_the_one_the_plan_prescribes(self, exhausted):
        assert exhausted[1].message.startswith(
            "We couldn't find an arrangement that works."
        )

    def test_it_never_says_impossible(self, exhausted):
        assert "impossible" not in exhausted[1].message.casefold()

    def test_one_specific_thing_is_offered(self, exhausted):
        relaxation = exhausted[1].relaxation
        assert relaxation is not None
        assert "?" in relaxation.question
        assert relaxation.labels

    def test_the_offer_names_furniture_and_never_a_wall(self, exhausted):
        graph, fixed = exhausted
        relaxation = fixed.relaxation
        assert relaxation.kind in ("unlock", "set_aside")
        assert all(graph.by_id(n).kind == "object" for n in relaxation.node_ids)

    def test_the_offer_is_the_owners_to_approve_not_ours_to_apply(self, exhausted):
        """Never quietly delete a chair. Plan section 10."""
        _, fixed = exhausted
        assert fixed.proposal is None
        assert fixed.graph is None


class TestASealedRoute:
    """A route with no way through reports no width at all."""

    @pytest.fixture
    def sealed(self, graph, scenario, pipeline, ledger, pack):
        from standardphysics_agents.evaluation.dataset import by_id

        case = by_id("blocked_but_movable")
        before = assess(case.graph, case.scenario, pipeline, rules=pack, ledger=ledger)
        blocked = [
            f for f in before.problems if f.check_id == "route_clear_width"
        ]
        assert blocked, "the sealed case has to seal the route"
        return case, before, blocked

    def test_the_barrier_starts_legal(self, sealed):
        """A barrier built through a wall is in breach before anything moves,
        so every rearrangement of it would be refused for the wrong reason."""
        case, _, _ = sealed
        assert violations(case.graph, case.graph) == []

    def test_no_width_is_reported(self, sealed):
        _, _, blocked = sealed
        assert all(f.measured_inches is None for f in blocked)

    def test_the_shortfall_is_the_whole_requirement(self, sealed):
        case, _, blocked = sealed
        pinch = pinch_from(blocked[0], case.graph)
        assert pinch.sealed
        assert pinch.deficit_meters == pytest.approx(to_meters(36.0))

    def test_widening_is_not_offered_because_it_cannot_work(self, sealed):
        """The two things forming the gap are already touching."""
        case, _, blocked = sealed
        strategies = {c.strategy for c in candidates(pinch_from(blocked[0], case.graph))}
        assert strategies <= {"stagger", "turn_one"}

    def test_it_finds_a_way_through(self, sealed, scenario, pipeline, pack, ledger):
        case, before, _ = sealed
        outcome = propose_fix(
            case.graph, case.scenario, pipeline, before.problems,
            rules=pack, ledger=ledger, baseline=before, limit=8,
        )
        assert outcome.found
        after = assess(
            outcome.graph, case.scenario, pipeline, rules=pack, ledger=ledger
        )
        assert not [
            f for f in after.problems if f.check_id == "route_clear_width"
        ]

    def test_fixed_shelving_is_still_somebody_elses_job(
        self, scenario, pipeline, pack, ledger
    ):
        from standardphysics_agents.evaluation.dataset import by_id

        case = by_id("blocked_solid")
        before = assess(case.graph, case.scenario, pipeline, rules=pack, ledger=ledger)
        outcome = propose_fix(
            case.graph, case.scenario, pipeline, before.problems,
            rules=pack, ledger=ledger, baseline=before, limit=8,
        )
        assert not outcome.found
        assert outcome.relaxation.kind == "unlock"


class TestMovingThingsThatShareFloorSpace:
    """Furniture overlaps from above all the time without touching."""

    def _room(self, *pieces):
        floor = _node("share_floor", "floor", "Floor", (0.0, 0.0, 0.0), (6.0, 6.0, 0.01), False)
        return SceneGraph(scan_id=node_id("share_room"), nodes=[floor, *pieces])

    def _table(self):
        return _node("share_table", "object", "Table", (0.0, 0.0, 0.375), (1.2, 0.8, 0.75), False)

    def _slide(self, graph, piece, dx, dy=0.0):
        return apply_moves(graph, [NodeMove(node_id=piece.id, delta_translation=Vec3(x=dx, y=dy, z=0.0))])

    def test_a_chair_tucked_under_a_table_can_be_pulled_out(self):
        chair = _node("share_chair", "object", "Chair", (0.3, 0.3, 0.45), (0.45, 0.45, 0.9), True)
        graph = self._room(self._table(), chair)
        assert "collided" not in _kinds(graph, self._slide(graph, chair, 0.0, 0.8))

    def test_a_chair_pushed_into_a_table_it_was_clear_of_still_collides(self):
        chair = _node("share_chair", "object", "Chair", (2.0, 0.0, 0.45), (0.45, 0.45, 0.9), True)
        graph = self._room(self._table(), chair)
        assert "collided" in _kinds(graph, self._slide(graph, chair, -2.0))

    def test_a_laptop_slides_across_the_desk_it_sits_on(self):
        laptop = _node("share_laptop", "object", "Laptop", (-0.3, 0.0, 0.765), (0.35, 0.25, 0.03), True)
        graph = self._room(self._table(), laptop)
        slid = self._slide(graph, laptop, 0.5)
        assert violations(graph, slid) == []
        assert slid.by_id(laptop.id).transform.position.z == pytest.approx(0.765)

    def test_a_pillow_lifted_off_a_table_settles_on_the_floor(self):
        pillow = _node("share_pillow", "object", "Pillow", (0.0, 0.0, 0.85), (0.4, 0.4, 0.2), True)
        graph = self._room(self._table(), pillow)
        dropped = self._slide(graph, pillow, 2.0)
        assert dropped.by_id(pillow.id).transform.position.z == pytest.approx(0.1)

    def test_a_pillow_carried_onto_another_table_lands_on_its_top(self):
        pillow = _node("share_pillow", "object", "Pillow", (0.0, 0.0, 0.85), (0.4, 0.4, 0.2), True)
        high_table = _node("share_high", "object", "Table", (2.0, 0.0, 0.5), (1.0, 1.0, 1.0), False)
        graph = self._room(self._table(), high_table, pillow)
        carried = self._slide(graph, pillow, 2.0)
        assert carried.by_id(pillow.id).transform.position.z == pytest.approx(1.1)
        assert violations(graph, carried) == []

    def test_a_chair_standing_on_the_floor_keeps_its_height(self):
        chair = _node("share_chair", "object", "Chair", (2.0, 0.0, 0.45), (0.45, 0.45, 0.9), True)
        graph = self._room(self._table(), chair)
        assert self._slide(graph, chair, 0.5).by_id(chair.id).transform.position.z == pytest.approx(0.45)

    def test_a_piece_the_scan_left_past_the_floor_edge_can_move_along_it(self):
        lamp = _node("share_lamp", "object", "Lamp", (2.95, 0.0, 0.6), (0.3, 0.3, 1.2), True)
        graph = self._room(lamp)
        assert "left_the_floor" not in _kinds(graph, self._slide(graph, lamp, 0.0, 0.5))
        assert "left_the_floor" in _kinds(graph, self._slide(graph, lamp, 0.5))
