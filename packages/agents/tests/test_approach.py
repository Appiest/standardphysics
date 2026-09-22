"""The approach evaluation, across six adversarial layouts.

Six layouts is the gate's own minimum, and each one here pins one failure mode
of a shortcut: endpoint-distance clearness, dropping a blocked aisle, ignoring
a turning envelope, overlooking a below-reach obstruction, promoting an
unobserved floor or an unmeasured height to a pass, and proposing a solution
that does not exist. The clear layout shows what a genuine pass looks like.
"""

from __future__ import annotations

import uuid

import pytest
from standardphysics_agents.assess import assess
from standardphysics_agents.fix.approach import (
    evaluate_approach,
    suggestion_stop,
    support_of,
)
from standardphysics_agents.fix.occupancy import (
    MANUAL_WHEELCHAIR,
    resize,
)
from standardphysics_agents.fix.search import propose_fix
from standardphysics_agents.rules import VerificationLedger, load_pack
from standardphysics_contracts import (
    Mat4,
    Scenario,
    SceneGraph,
    SceneNode,
    Stop,
    Vec3,
)

NAMESPACE = uuid.UUID("6f1d2f9a-0d3f-4a1e-9b2c-7ef00a0a0001")

ROOM = 5.0
WALL_T, WALL_H = 0.1, 3.0
TEST_REVIEWER = "test suite, not a person"


def node_id(name: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, name)


def _box(name, kind, label, category, center, dims, movable, parent=None):
    node = SceneNode(
        id=node_id(name),
        kind=kind,
        label=label,
        raw_category=category,
        dimensions=Vec3(x=dims[0], y=dims[1], z=dims[2]),
        transform=Mat4.translation(*center),
        movable=movable,
    )
    if parent is not None:
        node = node.model_copy(update={"parent_id": parent})
    return node


def _room(name: str, door_x: float = 0.0) -> SceneGraph:
    """A square room with four named walls, a floor and a door.

    `door_x` moves the door off the middle so a test can force the route to
    cross a pinch instead of starting already past it.
    """
    half = ROOM / 2
    z = WALL_H / 2
    walls = [
        _box(f"{name}_south", "wall", f"{name} south wall", "wall", (0.0, -half, z), (ROOM, WALL_T, WALL_H), False),
        _box(f"{name}_north", "wall", f"{name} north wall", "wall", (0.0, half, z), (ROOM, WALL_T, WALL_H), False),
        _box(f"{name}_west", "wall", f"{name} west wall", "wall", (-half, 0.0, z), (WALL_T, ROOM, WALL_H), False),
        _box(f"{name}_east", "wall", f"{name} east wall", "wall", (half, 0.0, z), (WALL_T, ROOM, WALL_H), False),
    ]
    floor = _box(f"{name}_floor", "floor", "Floor", "floor", (0.0, 0.0, 0.0), (ROOM, ROOM, 0.01), False)
    door = _box(
        f"{name}_door", "door", "Door", "door",
        (door_x, -half, 1.05), (0.9, WALL_T, 2.1), False,
    )
    return SceneGraph(scan_id=node_id(name), revision=0, nodes=[*walls, floor, door])


def _outlet(graph: SceneGraph, on_wall: str) -> SceneNode:
    """An outlet mounted in the named wall's inner face, top at 20 inches.

    The node sits half a wall thickness inside the wall centre, which is how a
    scan reports a small wall-mounted object.
    """
    wall = next(node for node in graph.nodes if node.label.endswith(f"{on_wall} wall"))
    against = {
        "south": (0.0, wall.transform.position.y + WALL_T / 2),
        "north": (0.0, wall.transform.position.y - WALL_T / 2),
        "west": (wall.transform.position.x + WALL_T / 2, 0.0),
        "east": (wall.transform.position.x - WALL_T / 2, 0.0),
    }[on_wall]
    return _box(
        "outlet", "outlet", "Outlet", "outlet",
        (against[0], against[1], 0.45), (0.08, 0.03, 0.12),
        False, parent=wall.id,
    )


def _start(graph: SceneGraph, x_offset: float = 0.05) -> Stop:
    door = next(node for node in graph.nodes if node.kind == "door")
    return Stop(
        name="Entrance",
        position=Vec3(x=door.transform.position.x, y=door.transform.position.y + x_offset, z=0.0),
        anchor_node_id=door.id,
    )


def _with(graph: SceneGraph, *nodes: SceneNode) -> SceneGraph:
    return graph.model_copy(update={"nodes": [*graph.nodes, *nodes]})


def _partitions(movable: bool, gap_meters: float = 0.5) -> tuple[SceneNode, SceneNode]:
    """A wall of two partitions across the room, with a gap in the middle.

    The partitions run from the side walls to `gap_meters` apart, forming the
    only route to the far side of the room. With a half-metre gap that is an
    aisle nobody's body fits through; slid apart it opens to code width.
    """
    half_gap = gap_meters / 2
    inner = 2.5 - WALL_T
    span = inner - half_gap
    centre = (inner + half_gap) / 2
    west = _box(
        "partition_west", "object", "Partition", "storage",
        (-centre, -1.5, 1.2), (span, 0.3, 2.4), movable,
    )
    east = _box(
        "partition_east", "object", "Partition", "storage",
        (centre, -1.5, 1.2), (span, 0.3, 2.4), movable,
    )
    return west, east


class FixtureMeasure:
    """The real pipeline measurement provider, not a stub.

    The approach must pass through the same grid the routes use; a hand-rolled
    stub would only prove this module against itself.
    """

    def __init__(self):
        from standardphysics_pipeline import PipelineMeasurements

        self._inner = PipelineMeasurements()

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.fixture(scope="module")
def graph():
    return _room("approach_room")


@pytest.fixture(scope="module")
def pack():
    return load_pack()


@pytest.fixture(scope="module")
def ledger(pack):
    book = VerificationLedger()
    for rule in pack.rules:
        book = book.record(rule, verified_by=TEST_REVIEWER)
    return book


class TestClearLayout:
    def test_an_open_room_outlet_is_clear_for_a_manual_wheelchair(self):
        graph = _room("clear_room")
        outlet = _outlet(graph, "west")
        result = evaluate_approach(
            graph, outlet, _start(graph),
            measure=FixtureMeasure(), occupants=(MANUAL_WHEELCHAIR,),
        )
        assert result.status == "clear", result.reasons
        assert result.path is not None
        assert result.aisle_width_inches >= MANUAL_WHEELCHAIR.travel_width_inches
        assert result.obstruction_labels == ()
        assert result.floor_supported is True
        assert result.reaches[0].status == "within_personal_reach"

    def test_the_wall_thin_attachment_is_not_a_solid_obstacle(self):
        """The wall the outlet hangs in occupies the grid. If the support is
        clear-exempt, the direct line reaches the socket; if not, every
        wall-side approach reads blocked."""
        graph = _room("attachment_room")
        outlet = _outlet(graph, "west")
        result = evaluate_approach(
            graph, outlet, _start(graph),
            measure=FixtureMeasure(), occupants=(MANUAL_WHEELCHAIR,),
        )
        assert result.status == "clear", result.reasons
        assert "below-reach obstruction" not in " ".join(result.reasons)

    def test_a_short_reach_profile_is_blocked_only_on_reach(self):
        graph = _room("reach_room")
        outlet = _outlet(graph, "west")
        reachy = resize(MANUAL_WHEELCHAIR, personal_reach_inches=12.0)
        standard = evaluate_approach(
            graph, outlet, _start(graph),
            measure=FixtureMeasure(), occupants=(MANUAL_WHEELCHAIR,),
        )
        narrow = evaluate_approach(
            graph, outlet, _start(graph),
            measure=FixtureMeasure(), occupants=(reachy,),
        )
        assert standard.status == "clear"
        assert narrow.status == "blocked"
        assert any("personal reach" in reason for reason in narrow.reasons)

    def test_adjusting_one_profile_never_changes_another(self):
        graph = _room("adjust_room")
        outlet = _outlet(graph, "west")
        wider = resize(MANUAL_WHEELCHAIR, body_width_inches=48.0)
        standard = evaluate_approach(
            graph, outlet, _start(graph),
            measure=FixtureMeasure(), occupants=(MANUAL_WHEELCHAIR,),
        )
        assert wider.body_width_inches != MANUAL_WHEELCHAIR.body_width_inches
        assert standard.status == "clear"
        assert MANUAL_WHEELCHAIR.body_width_inches == 26.0


class TestBlockedAisle:
    def test_an_aisle_narrower_than_the_body_is_blocked(self):
        """Two partitions leave a 50 cm aisle, 19.7 inches: the grid can
        thread it, the occupant cannot. Endpoint distance alone would call
        the room reachable."""
        graph = _room("aisle_room", door_x=1.4)
        room = _with(graph, *_partitions(movable=False))
        outlet = _outlet(room, "west")
        result = evaluate_approach(
            room, outlet, _start(room),
            measure=FixtureMeasure(), occupants=(MANUAL_WHEELCHAIR,),
        )
        assert result.status == "blocked"
        assert any("aisle too narrow" in reason for reason in result.reasons)
        assert result.aisle_width_inches is not None
        assert result.aisle_width_inches < MANUAL_WHEELCHAIR.travel_width_inches


class TestTightTurningCorner:
    def test_a_dead_end_aisle_the_body_fits_through_blocks_on_turning(self):
        """The aisle is 31 inches, which the body passes through, but there is
        no circle anywhere on the arrival to turn around in."""
        graph = _room("turn_room")
        west = _box("aisle_west", "object", "Partition", "storage", (-0.55, 0.0, 1.2), (0.3, 4.9, 2.4), False)
        east = _box("aisle_east", "object", "Partition", "storage", (0.55, 0.0, 1.2), (0.3, 4.9, 2.4), False)
        room = _with(graph, west, east)
        outlet = _outlet(room, "north")
        result = evaluate_approach(
            room, outlet, _start(room),
            measure=FixtureMeasure(), occupants=(MANUAL_WHEELCHAIR,),
        )
        assert result.status == "blocked"
        assert any("no turning space on the arrival" in reason for reason in result.reasons)
        assert result.aisle_width_inches >= MANUAL_WHEELCHAIR.travel_width_inches


class TestObstacleBelowReach:
    def test_furniture_pushed_in_front_of_the_outlet_blocks(self):
        """A sofa against the wall under the socket keeps the approach
        blocked even though the socket itself is untouched: the arm line from
        any lawful standing point crosses the sofa."""
        graph = _room("sofa_room")
        outlet = _outlet(graph, "west")
        sofa = _box("sofa", "object", "Sofa", "sofa", (-1.95, 0.0, 0.4), (0.9, 2.0, 0.8), True)
        room = _with(graph, sofa)
        result = evaluate_approach(
            room, outlet, _start(room),
            measure=FixtureMeasure(), occupants=(MANUAL_WHEELCHAIR,),
        )
        assert result.status == "blocked"
        assert "Sofa" in result.obstruction_labels
        assert any("below-reach obstruction" in reason for reason in result.reasons)

    def test_a_support_surface_is_not_an_obstruction(self):
        graph = _room("support_room")
        outlet = _outlet(graph, "west")
        support = support_of(graph, outlet)
        wall = next(node for node in graph.nodes if node.label.endswith("west wall"))
        assert wall.id in support
        assert outlet.id in support
        assert len(support) == 2


class TestUnobservedInputs:
    def test_an_unobserved_floor_is_needs_verification(self):
        graph = _room("no_floor_room")
        walls_only = graph.model_copy(
            update={"nodes": [n for n in graph.nodes if n.kind != "floor"]}
        )
        outlet = _outlet(walls_only, "west")
        result = evaluate_approach(
            walls_only, outlet, _start(walls_only),
            measure=FixtureMeasure(), occupants=(MANUAL_WHEELCHAIR,),
        )
        assert result.status == "needs_verification"
        assert any("floor is unobserved" in reason for reason in result.reasons)

    def test_an_unmeasured_height_is_needs_verification(self):
        graph = _room("flat_outlet_room")
        outlet = _outlet(graph, "west").model_copy(
            update={"dimensions": Vec3(x=0.08, y=0.03, z=0.0)}
        )
        result = evaluate_approach(
            graph, outlet, _start(graph),
            measure=FixtureMeasure(), occupants=(MANUAL_WHEELCHAIR,),
        )
        assert result.status == "needs_verification"
        assert result.reaches[0].status == "unmeasured"
        assert any("unmeasured" in reason for reason in result.reasons)

    def test_an_unassumed_personal_reach_is_needs_verification(self):
        graph = _room("no_reach_room")
        outlet = _outlet(graph, "west")
        no_reach = resize(MANUAL_WHEELCHAIR, personal_reach_inches=None)
        result = evaluate_approach(
            graph, outlet, _start(graph),
            measure=FixtureMeasure(), occupants=(no_reach,),
        )
        assert result.status == "needs_verification"
        assert result.reaches[0].status == "unmeasured"

    def test_missing_inputs_are_never_promoted_to_clear(self):
        graph = _room("never_clear_room")
        no_floor = graph.model_copy(
            update={"nodes": [n for n in graph.nodes if n.kind != "floor"]}
        )
        outlet = _outlet(no_floor, "west").model_copy(
            update={"dimensions": Vec3(x=0.08, y=0.03, z=0.0)}
        )
        result = evaluate_approach(
            no_floor, outlet, _start(no_floor),
            measure=FixtureMeasure(), occupants=(MANUAL_WHEELCHAIR,),
        )
        assert result.status in ("blocked", "needs_verification")
        assert result.status != "clear"


class TestRedesignOnApproachFailure:
    """Case 6: a movable blocker gets a gated proposal; a fixed one does not
    get a fabricated solution out of the same search."""

    def _approach_scenario(self, graph: SceneGraph) -> Scenario:
        return Scenario(
            name="Approach the outlet",
            stops=[
                _start(graph),
                Stop(name="At the outlet", position=Vec3(x=-1.6, y=0.0, z=0.0)),
            ],
        )

    def test_a_movable_blocker_gets_a_gated_proposal(self, pack, ledger):
        graph = _room("movable_room", door_x=1.4)
        room = _with(graph, *_partitions(movable=True))
        outlet = _outlet(room, "west")
        # The approach itself is blocked by the aisle pinch.
        approach = evaluate_approach(
            room, outlet, _start(room),
            measure=FixtureMeasure(), occupants=(MANUAL_WHEELCHAIR,),
        )
        assert approach.status == "blocked"

        # The same machinery that blocked the approach now sizes the fix: the
        # pinch movables are the partitions, and a gated proposal widens it.
        scenario = self._approach_scenario(room)
        before = assess(room, scenario, FixtureMeasure(), rules=pack, ledger=ledger)
        sized = [f for f in before.problems if f.check_id == "route_clear_width"]
        assert sized, "expected the aisle pinch to be a route_clear_width problem"
        outcome = propose_fix(
            room, scenario, FixtureMeasure(), sized,
            rules=pack, ledger=ledger, baseline=before, offer_relaxation=False,
        )
        assert outcome.found, outcome.message
        from standardphysics_agents.evaluation.gate import accepts

        after = assess(outcome.graph, scenario, FixtureMeasure(), rules=pack, ledger=ledger)
        assert accepts(before, after)

    def test_a_fixed_counter_blocks_and_no_furniture_move_exists(self, pack, ledger):
        graph = _room("fixed_room", door_x=1.4)
        room = _with(graph, *_partitions(movable=False))
        outlet = _outlet(room, "west")
        approach = evaluate_approach(
            room, outlet, _start(room),
            measure=FixtureMeasure(), occupants=(MANUAL_WHEELCHAIR,),
        )
        assert approach.status == "blocked"

        scenario = self._approach_scenario(room)
        before = assess(room, scenario, FixtureMeasure(), rules=pack, ledger=ledger)
        sized = [f for f in before.problems if f.check_id == "route_clear_width"]
        assert sized
        outcome = propose_fix(
            room, scenario, FixtureMeasure(), sized, baseline=before,
            rules=pack, ledger=ledger, offer_relaxation=False,
        )
        assert not outcome.found
        assert outcome.graph is None


class TestSuggestedStops:
    def test_a_suggested_stop_stands_on_the_scanned_floor(self):
        graph = _room("suggest_room")
        outlet = _outlet(graph, "west")
        stop = suggestion_stop(graph, outlet, (MANUAL_WHEELCHAIR,))
        assert stop is not None
        assert abs(stop.x) <= ROOM / 2 and abs(stop.y) <= ROOM / 2
        assert abs(stop.x + 2.4) >= 0.25, "the stop must stand off the wall"

    def test_a_suggested_stop_is_reachable_from_the_confirmed_start(self):
        graph = _room("suggest_route_room")
        outlet = _outlet(graph, "west")
        stop = suggestion_stop(graph, outlet, (MANUAL_WHEELCHAIR,))
        assert stop is not None
        result = evaluate_approach(
            graph, outlet, _start(graph),
            measure=FixtureMeasure(), occupants=(MANUAL_WHEELCHAIR,),
            approach_stop=stop,
        )
        assert result.path is not None
