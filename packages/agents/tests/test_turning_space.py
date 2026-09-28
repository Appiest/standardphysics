"""Where the turning circle may sit, and which stops need one at all."""

from types import SimpleNamespace

import pytest
from standardphysics_agents.checks.turning_space import turning_space
from standardphysics_agents.rules import load_pack
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Stop, Vec3, to_inches, to_meters
from standardphysics_fixtures.stub_measurements import FixtureMeasurements

SCAN = "00000000-0000-0000-0000-00000000c0de"
COUNTER_GAP = 0.6
"""A pickup stop 23.6 in from the counter it serves, as the shop generator places it."""


def _box(name: str, centre: tuple[float, float], size: tuple[float, float]) -> SceneNode:
    return SceneNode(id=f"00000000-0000-0000-0000-{abs(hash(name)) % 10**12:012d}", kind="object", label=name,
                     raw_category=name, dimensions=Vec3(x=size[0], y=size[1], z=1.0),
                     transform=Mat4.translation(centre[0], centre[1], 0.5), movable=False)


def _ctx(extra: list[SceneNode], destination: str = "Restroom"):
    counter = _box("Counter", (0.0, COUNTER_GAP + 0.3), (3.0, 0.6))
    graph = SceneGraph(scan_id=SCAN, nodes=[counter, *extra])
    stops = [Stop(name="Entrance", position=Vec3(x=-2.5, y=0.0, z=0.0)),
             Stop(name=destination, position=Vec3(x=0.0, y=0.0, z=0.0)),
             Stop(name="Exit", position=Vec3(x=-2.5, y=0.0, z=0.0))]
    pack = load_pack()
    return SimpleNamespace(graph=graph, scenario=SimpleNamespace(stops=stops), measure=FixtureMeasurements(),
                           rule=pack.by_id)


def test_a_counter_reached_from_the_side_passes_when_the_floor_behind_the_stop_is_clear():
    """The head-on setback spot sits beside the counter and holds only 47 in; a circle just off the
    counter, still containing the stop, holds 60 in."""
    [observation] = turning_space(_ctx([]))
    assert observation.satisfied
    assert observation.measured_inches >= 60.0


def test_no_room_anywhere_near_the_stop_still_fails_at_the_setback_spot():
    """A bench 0.55 m behind the stop leaves 1.15 m between it and the counter, short of a 60 in circle."""
    bench = _box("Bench", (0.0, -0.75), (4.0, 0.4))
    [observation] = turning_space(_ctx([bench]))
    assert not observation.satisfied
    assert observation.measured_inches == pytest.approx(to_inches(0.55 * 2), abs=0.5)
    assert (observation.locus.point.x, observation.locus.point.y) == pytest.approx((-to_meters(30.0), 0.0))


@pytest.mark.parametrize("destination", ["Pickup", "Seats", "Counter", "Shelves"])
def test_a_dead_end_on_the_shop_floor_asks_for_no_turning_space(destination):
    """304.3 applies where a room's own section calls for it; a pickup counter or a seat is not one."""
    bench = _box("Bench", (0.0, -0.75), (4.0, 0.4))
    assert turning_space(_ctx([bench], destination)) == []


@pytest.mark.parametrize("destination", ["Restroom", "Fitting room", "Dressing room"])
def test_a_restroom_or_fitting_room_is_checked_for_turning_space(destination):
    [observation] = turning_space(_ctx([], destination))
    assert observation.facts["stop"] == destination
