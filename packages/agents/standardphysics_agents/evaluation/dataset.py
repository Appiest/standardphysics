"""The labelled cases the checks are scored against.

A case says what a correct answer looks like, and it says so from the standard
rather than from what the code currently returns. Where the two disagree, one
of them is wrong and the point of the dataset is to make that visible. It has
already earned its keep twice: it caught a turn reported as zero inches wide
where there was no route either side of it to measure, and the same corner
reported twice on an out-and-back errand.

Almost every case starts from `_base()`, which removes the seating by the
counter to isolate the geometry each case varies. The shipped shop's route
passes through open floor beside that seating; the diagonal distance from a
table to the counter is not a corridor width. `fixture_as_shipped` retains
all furniture and expects one deduplicated aisle pinch and the high counter.

Every case carries the scan-cannot-see items, because those are asked
of every shop. What varies is the geometry, the labels, how sure we are of
them, and what the loop should do next.

`actions_taken` is how a case reaches a later stage of the loop. The router
never escalates before it has asked, so a case that should escalate has to say
that asking already happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from standardphysics_contracts import Scenario, SceneGraph, Stop, Vec3
from standardphysics_contracts.loop import RouterAction
from standardphysics_contracts.rules import Tier
from standardphysics_fixtures import (
    build_graph,
    build_lawsuit_graph,
    build_lawsuit_scenario,
    build_scenario,
)
from standardphysics_fixtures.shop import build_street_scenario, node_id

from . import variants as v

SCAN_CANNOT_SEE = frozenset(
    {
        "entrance_threshold",
        "door_hardware",
        "door_opening_force",
        "floor_surface",
        "restroom_turning_space",
    }
)

COUNTER = v.COUNTER
DOOR = v.DOOR
CASE_WEST = v.CASE_WEST
CASE_EAST = v.CASE_EAST

FIXTURE_COUNTER_INCHES = 47.0
"""The fixture counter's height, from Whitaker v. T Rock Inc., against the 36 in that 904.4.1 allows."""

DOORWAY_INCHES = 35.433
"""The fixture's front door opening. Wide enough for 404.2.3's 32 in, and
narrower than the 36 in a route needs, which is why walking in from the street
reports something that walking from just inside the door does not."""

DEFAULT_ROLES: dict[str, frozenset[UUID]] = {
    "service_counter": frozenset({COUNTER}),
    "entrance": frozenset({DOOR}),
}

NO_COUNTER_ROLE: dict[str, frozenset[UUID]] = {
    "service_counter": frozenset(),
    "entrance": frozenset({DOOR}),
}

NO_ROLES: dict[str, frozenset[UUID]] = {
    "service_counter": frozenset(),
    "entrance": frozenset(),
}

COUNTER_TOO_HIGH = frozenset({"service_counter_height"})
POS_ON_HIGH_COUNTER = frozenset({"point_of_sale_height"})

ROUTE = "route_clear_width"


@dataclass(frozen=True)
class Case:
    id: str
    description: str
    graph: SceneGraph
    scenario: Scenario
    expected_problems: frozenset[str] = frozenset()
    forbidden_problems: frozenset[str] = frozenset()
    """Checks that must not fire. This is where a false positive gets caught."""

    expected_questions: frozenset[str] = SCAN_CANNOT_SEE
    expected_inches: dict[str, float] = field(default_factory=dict)
    """Check id to the measurement the standard would take at its tightest."""

    expected_roles: dict[str, frozenset[UUID]] = field(
        default_factory=lambda: dict(DEFAULT_ROLES)
    )
    expected_action: RouterAction | None = None
    actions_taken: tuple[RouterAction, ...] = ()
    fix_should_resolve: bool = False
    max_tier: Tier = 1
    """Which tiers to run. Tier 1 unless the case is about a tier 2 or 3 rule."""


def _case(case_id: str, description: str, graph, scenario=None, **labels) -> Case:
    return Case(
        id=case_id,
        description=description,
        graph=graph,
        scenario=scenario or build_scenario(),
        **labels,
    )


def _base() -> SceneGraph:
    """The fixture shop with the counter-side seating cleared."""
    return v.clear_counter_side(build_graph())


def _open() -> SceneGraph:
    """A 48 in aisle, and the fixture's own 47 in counter."""
    return v.aisle(_base(), 48.0)


def _clean() -> SceneGraph:
    """Wide aisle, a counter somebody in a wheelchair can order from."""
    return v.counter_height(_open(), 34.0)


def _aisle_cases() -> list[Case]:
    """ADA 2010 403.5.1, either side of its thresholds."""
    return [
        _case(
            "aisle_31",
            "The fixture's own pinch. 31 in against a 36 in minimum.",
            _base(),
            expected_problems=frozenset({ROUTE}) | COUNTER_TOO_HIGH,
            expected_inches={ROUTE: 31.0, "service_counter_height": FIXTURE_COUNTER_INCHES},
            expected_action="FIX",
            fix_should_resolve=True,
        ),
        _case(
            "aisle_24",
            "Two feet across. Below even the reduced width the exception allows.",
            v.aisle(_base(), 24.0),
            expected_problems=frozenset({ROUTE}) | COUNTER_TOO_HIGH,
            expected_inches={ROUTE: 24.0},
            expected_action="FIX",
            fix_should_resolve=True,
        ),
        _case(
            "aisle_36",
            "Exactly the minimum, which the section permits.",
            v.aisle(_base(), 36.0),
            expected_problems=COUNTER_TOO_HIGH,
            forbidden_problems=frozenset({ROUTE}),
            expected_inches={ROUTE: 36.0},
            expected_action="ASK_OWNER",
        ),
        _case(
            "aisle_35_9",
            "A tenth of an inch short. A rounded label must not rescue it.",
            v.aisle(_base(), 35.9),
            expected_problems=frozenset({ROUTE}) | COUNTER_TOO_HIGH,
            expected_inches={ROUTE: 35.9},
            expected_action="FIX",
            fix_should_resolve=True,
        ),
        _case(
            "aisle_33_long_run",
            "33 in, inside the band 403.5.1's exception covers, but the narrow "
            "stretch runs 167 in and the exception allows 24.",
            v.aisle(_base(), 33.0),
            expected_problems=frozenset({ROUTE}) | COUNTER_TOO_HIGH,
            expected_inches={ROUTE: 33.0},
            expected_action="FIX",
            fix_should_resolve=True,
        ),
        _case(
            "aisle_60",
            "Wide enough that passing space stops applying as well.",
            v.counter_height(v.aisle(_base(), 60.0), 34.0),
            forbidden_problems=frozenset({ROUTE, "passing_space"}),
            expected_inches={ROUTE: 60.0},
            expected_action="ASK_OWNER",
        ),
    ]


def _door_cases() -> list[Case]:
    """ADA 2010 404.2.3."""
    return [
        _case(
            "door_30",
            "A 30 in doorway. Nothing on wheels gets through it.",
            v.door_width(_clean(), 30.0),
            expected_problems=frozenset({"door_clear_width"}),
            expected_inches={"door_clear_width": 30.0},
            # An opening under the requirement is certain: the door in it only
            # makes the gap smaller, so this one is measured, not asked about.
            expected_action="ASK_OWNER",
        ),
        _case(
            "door_32",
            "An opening of exactly 32 in. The hole clears the requirement, and "
            "the door in it does not, so this is asked about rather than passed.",
            v.door_width(_clean(), 32.0),
            forbidden_problems=frozenset({"door_clear_width"}),
            expected_action="ASK_OWNER",
        ),
        _case(
            "no_door",
            "A scan that caught no doorway. The check has nothing to measure "
            "and must not report a pass on a door it never saw.",
            v.drop(_clean(), DOOR),
            forbidden_problems=frozenset({"door_clear_width"}),
            expected_roles=dict(NO_ROLES, service_counter=frozenset({COUNTER})),
            expected_action="ASK_OWNER",
        ),
        _case(
            "street_approach",
            "Walking in from the pavement, so the 35 in doorway is on the "
            "route rather than the place the route starts.",
            _clean(),
            build_street_scenario(),
            expected_problems=frozenset({ROUTE}),
            expected_inches={ROUTE: DOORWAY_INCHES},
            expected_action="ASK_OWNER",
        ),
    ]


def _counter_cases() -> list[Case]:
    """ADA 2010 904.4.1 and the 305.3 space it refers to."""
    return [
        _case(
            "counter_43",
            "The fixture counter at 47 in, against the 36 in maximum.",
            _open(),
            expected_problems=COUNTER_TOO_HIGH,
            expected_inches={"service_counter_height": FIXTURE_COUNTER_INCHES},
            expected_action="ASK_OWNER",
        ),
        _case(
            "counter_36",
            "Exactly 36 in, which the section permits.",
            v.counter_height(_open(), 36.0),
            forbidden_problems=COUNTER_TOO_HIGH,
            expected_inches={"service_counter_height": 36.0},
            expected_action="ASK_OWNER",
        ),
        _case(
            "counter_blocked",
            "A low counter with a display case parked in front of it. There is "
            "nowhere to pull up, while the customer route remains wide enough.",
            v.add(
                _clean(),
                v.box("blocker", "Display case", (0.5, 2.87, 0.45), (0.5, 0.5, 0.9)),
            ),
            expected_problems=frozenset({"service_counter_approach"}),
            forbidden_problems=COUNTER_TOO_HIGH | {ROUTE},
            expected_action="FIX",
            fix_should_resolve=True,
        ),
        _case(
            "counter_mislabelled",
            "Astra called the ordering counter a cabinet. Nothing resolves to "
            "a service counter, so 904.4.1 has nothing to run against and the "
            "check reports nothing rather than measuring a random box.",
            v.relabel(_clean(), COUNTER, "Cabinet"),
            forbidden_problems=frozenset(
                {"service_counter_height", "service_counter_approach"}
            ),
            expected_roles=NO_COUNTER_ROLE,
            expected_action="ASK_OWNER",
        ),
        _case(
            "counter_labelled_bar",
            "The same counter labelled a bar, which is still a service counter "
            "and still 47 in high.",
            v.relabel(_open(), COUNTER, "Bar"),
            expected_problems=COUNTER_TOO_HIGH,
            expected_inches={"service_counter_height": FIXTURE_COUNTER_INCHES},
            expected_action="ASK_OWNER",
        ),
    ]


def _coverage_cases() -> list[Case]:
    """Thin coverage becomes a request, never a red finding."""
    return [
        _case(
            "thin_case_east",
            "One display case needs another look, so the pinch it forms is a "
            "request. Passing space rests on the same geometry and follows.",
            v.set_quality(_base(), CASE_EAST, "needs_another_look"),
            expected_problems=COUNTER_TOO_HIGH,
            forbidden_problems=frozenset({ROUTE, "passing_space"}),
            expected_questions=SCAN_CANNOT_SEE | {ROUTE, "passing_space"},
            expected_action="RESCAN_AREA",
        ),
        _case(
            "thin_counter",
            "The counter needs another look, so its height and its approach "
            "are both requests.",
            v.set_quality(_open(), COUNTER, "needs_another_look"),
            forbidden_problems=frozenset(
                {"service_counter_height", "service_counter_approach"}
            ),
            expected_questions=SCAN_CANNOT_SEE
            | {"service_counter_height", "service_counter_approach", ROUTE},
            expected_action="RESCAN_AREA",
        ),
        _case(
            "thin_and_narrow",
            "Thin coverage beside a 24 in gap. Still a request: a number we "
            "are not sure of is not evidence, however bad it looks.",
            v.set_quality(v.aisle(_base(), 24.0), CASE_WEST, "needs_another_look"),
            expected_problems=COUNTER_TOO_HIGH,
            forbidden_problems=frozenset({ROUTE}),
            expected_questions=SCAN_CANNOT_SEE | {ROUTE, "passing_space"},
            expected_action="RESCAN_AREA",
        ),
        _case(
            "confirmed_by_hand",
            "A number a person entered by hand counts as measured, so the "
            "same pinch is a problem again.",
            v.set_quality(_base(), CASE_EAST, "confirmed"),
            expected_problems=frozenset({ROUTE}) | COUNTER_TOO_HIGH,
            expected_inches={ROUTE: 31.0},
            expected_action="FIX",
            fix_should_resolve=True,
        ),
    ]


def _route_shape_cases() -> list[Case]:
    """Rules that only apply in particular places."""
    nook = v.add(
        _clean(),
        v.box("nook_west", "Shelf", (-1.0, 1.0, 0.5), (1.9, 0.5, 1.0)),
        v.box("nook_east", "Shelf", (1.0, 1.0, 0.5), (1.9, 0.5, 1.0)),
    )
    errand = v.errand(
        "Restroom", there=Vec3(x=0.0, y=2.4, z=0.0), back=Vec3(x=0.0, y=-3.5, z=0.0)
    )
    return [
        _case(
            "dead_end_tight",
            "An out and back errand into a corner with no room to turn round. "
            "This is where 304.3 applies, and 403.5.2 catches the two shelves "
            "the route has to come back around.",
            nook,
            errand,
            expected_problems=frozenset({"turning_space", "turn_clear_width", ROUTE}),
            expected_action="FIX",
        ),
        _case(
            "dead_end_roomy",
            "The same errand across an open floor. There is room to turn, so "
            "nothing is reported.",
            _clean(),
            errand,
            forbidden_problems=frozenset({"turning_space", ROUTE}),
            expected_action="ASK_OWNER",
        ),
        _case(
            "no_dead_end",
            "The shop's own route never doubles back, so 304.3 does not apply "
            "and nothing about turning round is reported.",
            _clean(),
            forbidden_problems=frozenset({"turning_space", "turn_clear_width", ROUTE}),
            expected_action="ASK_OWNER",
        ),
        _case(
            "tight_alcove",
            "Fixed shelves narrowing the walkway from both sides on the way in.",
            v.add(
                _clean(),
                v.box("alcove_west", "Shelf", (-1.1, -1.2, 0.5), (1.7, 0.5, 1.0), movable=False),
                v.box("alcove_east", "Shelf", (1.1, -1.2, 0.5), (1.7, 0.5, 1.0), movable=False),
            ),
            expected_problems=frozenset({ROUTE, "turn_clear_width"}),
            expected_action="FIX",
        ),
        _case(
            "passing_space_absent",
            "A 38 in corridor with nowhere along it to step aside. Wide enough "
            "to travel, too narrow for two people to pass, and 403.5.3 wants a "
            "60 in square somewhere.",
            _corridor(),
            _corridor_scenario(),
            expected_problems=frozenset({"passing_space"}),
            forbidden_problems=frozenset({ROUTE}),
            expected_roles=NO_ROLES,
            expected_action="ASK_OWNER",
        ),
    ]


BARRIER_HALF_WIDTH = 2.95
"""Two shelves this wide meet in the middle of the fixture room and seal it,
with both of them still inside their own walls. A barrier built through a wall
is in breach before anything moves, so every rearrangement of it is refused for
the wrong reason."""


def _barrier(movable: bool) -> SceneGraph:
    """A sealed room with nothing else near the barrier.

    The display cases and the counter-side seating come out, so a rearrangement
    of the barrier is tested against the barrier rather than against whatever
    else the fixture happens to leave in the way.
    """
    bare = v.drop(_clean(), CASE_WEST, CASE_EAST)
    centre = BARRIER_HALF_WIDTH / 2
    return v.add(
        bare,
        v.box(
            "bar_west", "Shelf", (-centre, 0.5, 0.6),
            (BARRIER_HALF_WIDTH, 0.6, 1.2), movable=movable,
        ),
        v.box(
            "bar_east", "Shelf", (centre, 0.5, 0.6),
            (BARRIER_HALF_WIDTH, 0.6, 1.2), movable=movable,
        ),
    )


def _blocked_cases() -> list[Case]:
    """No way through, and whether furniture could open one."""
    return [
        _case(
            "blocked_solid",
            "Fixed shelving sealing the only route. Nothing movable causes it, "
            "the owner has already been asked, and it belongs with a "
            "professional.",
            _barrier(movable=False),
            expected_problems=frozenset({ROUTE, "exit_path"}),
            expected_action="ESCALATE",
            actions_taken=("ASK_OWNER",),
        ),
        _case(
            "blocked_but_movable",
            "The same barrier, unlocked. A sealed route reports no width at "
            "all, which is a shortfall of the whole 36 inches rather than an "
            "unknown, so the shelves get stepped past each other until a way "
            "through opens.",
            _barrier(movable=True),
            expected_problems=frozenset({ROUTE, "exit_path"}),
            expected_action="FIX",
            fix_should_resolve=True,
        ),
    ]


def _quiet_cases() -> list[Case]:
    """Shops where the right answer is to ask, or to stop."""
    clean = _clean()
    return [
        _case(
            "clean_shop",
            "Wide aisle, low counter, wide door. Nothing measurable is wrong, "
            "and the only thing left is the photographs.",
            clean,
            forbidden_problems=frozenset(
                {ROUTE, "door_clear_width", "service_counter_height", "passing_space", "exit_path"}
            ),
            expected_action="ASK_OWNER",
        ),
        _case(
            "clean_shop_already_asked",
            "The same shop after the owner has been asked. Nothing is left but "
            "rendering the report.",
            clean,
            forbidden_problems=frozenset({ROUTE, "service_counter_height"}),
            expected_action="DONE",
            actions_taken=("ASK_OWNER",),
        ),
        _case(
            "empty_room",
            "Four walls, a door and a floor. No counter, no seating, nothing "
            "to get past.",
            v.drop(
                _base(),
                COUNTER,
                CASE_WEST,
                CASE_EAST,
                node_id("table_3"),
                node_id("table_4"),
                node_id("chair_5"),
                node_id("chair_6"),
            ),
            forbidden_problems=frozenset({ROUTE, "service_counter_height", "turning_space"}),
            expected_roles=dict(NO_ROLES, entrance=frozenset({DOOR})),
            expected_action="ASK_OWNER",
        ),
        _case(
            "fixture_as_shipped",
            "The demo shop exactly as the fixtures build it, band of seating "
            "by the counter and all. One aisle pinch and a high counter.",
            build_graph(),
            expected_problems=frozenset({ROUTE}) | COUNTER_TOO_HIGH,
            expected_action="FIX",
            fix_should_resolve=True,
        ),
    ]


def _crowding_cases() -> list[Case]:
    """Furniture in the way, which is the common real problem."""
    clean = _clean()
    return [
        _case(
            "tables_crowd_the_aisle",
            "Two tables pushed out into the walkway, which is the most "
            "ordinary way a shop stops being accessible.",
            v.add(
                clean,
                v.box("crowd_west", "Table", (-0.55, -1.5, 0.375), (0.6, 0.6, 0.75)),
                v.box("crowd_east", "Table", (0.55, -1.5, 0.375), (0.6, 0.6, 0.75)),
            ),
            expected_problems=frozenset({ROUTE}),
            expected_action="FIX",
            fix_should_resolve=True,
        ),
        _case(
            "one_chair_out",
            "A single chair left out near a wall, with the whole room to walk "
            "round it. Not a finding.",
            v.add(clean, v.box("stray_chair", "Chair", (-2.3, -1.5, 0.45), (0.45, 0.45, 0.9))),
            forbidden_problems=frozenset({ROUTE}),
            expected_action="ASK_OWNER",
        ),
    ]


def _corridor() -> SceneGraph:
    """A 38 in corridor with a wall either side and no wide spot in it."""
    nodes = [
        v.box("corridor_floor", "Floor", (0.0, 0.0, 0.0), (3.0, 10.0, 0.01),
              kind="floor", movable=False, raw_category="floor"),
        v.box("corridor_west", "Wall", (-0.532, 0.0, 1.5), (0.1, 10.0, 3.0),
              kind="wall", movable=False, raw_category="wall"),
        v.box("corridor_east", "Wall", (0.532, 0.0, 1.5), (0.1, 10.0, 3.0),
              kind="wall", movable=False, raw_category="wall"),
        v.box("corridor_north", "Wall", (0.0, 5.0, 1.5), (3.0, 0.1, 3.0),
              kind="wall", movable=False, raw_category="wall"),
        v.box("corridor_south", "Wall", (0.0, -5.0, 1.5), (3.0, 0.1, 3.0),
              kind="wall", movable=False, raw_category="wall"),
    ]
    return SceneGraph(scan_id=node_id("corridor"), nodes=nodes)


def _corridor_scenario() -> Scenario:
    return Scenario(
        name="Walk the corridor",
        stops=[
            Stop(name="Entrance", position=Vec3(x=0.0, y=-4.5, z=0.0)),
            Stop(name="Counter", position=Vec3(x=0.0, y=4.5, z=0.0)),
        ],
    )


def _tier_2_and_3_cases() -> list[Case]:
    """Rules that land once route measurement works, per plan section 8."""
    clean = _clean()
    shelf_at_head_height = v.add(
        clean,
        v.box("wall_shelf", "Shelf", (-2.8, 1.5, 1.1), (0.3, 1.2, 0.3), movable=False),
    )
    return [
        _case(
            "door_clearance_clear",
            "Nothing in front of the door, so there is room to pull it open "
            "from a wheelchair whichever way it swings.",
            clean,
            forbidden_problems=frozenset({"door_maneuvering_clearance"}),
            expected_questions=SCAN_CANNOT_SEE | {"reach_range"},
            expected_action="ASK_OWNER",
            max_tier=3,
        ),
        _case(
            "door_clearance_blocked",
            "A chair left in the doorway, so there is not enough floor in "
            "front to open the door from a wheelchair at all.",
            v.add(clean, v.box("blocker", "Chair", (0.0, -3.3, 0.45), (0.45, 0.45, 0.9))),
            expected_problems=frozenset({"door_maneuvering_clearance"}),
            forbidden_problems=frozenset({ROUTE}),
            expected_questions=SCAN_CANNOT_SEE | {"reach_range"},
            expected_action="ASK_OWNER",
            max_tier=3,
        ),
        _case(
            "protrusion_sticks_out",
            "A shelf mounted at head height reaching a foot off the wall, "
            "which a cane sweeping the floor never finds.",
            shelf_at_head_height,
            expected_problems=frozenset({"protruding_objects"}),
            expected_inches={"protruding_objects": 11.8},
            expected_questions=SCAN_CANNOT_SEE | {"reach_range"},
            expected_action="ASK_OWNER",
            max_tier=3,
        ),
        _case(
            "protrusion_tucked_in",
            "The same shelf, shallow enough to stay out of the way.",
            v.add(
                clean,
                v.box(
                    "wall_shelf", "Shelf", (-2.9119, 1.5, 1.1),
                    (0.0762, 1.2, 0.3), movable=False,
                ),
            ),
            forbidden_problems=frozenset({"protruding_objects"}),
            expected_questions=SCAN_CANNOT_SEE | {"reach_range"},
            expected_action="ASK_OWNER",
            max_tier=3,
        ),
        _case(
            "tables_are_a_usable_height",
            "Tables at 29 inches, inside the 28 to 34 inch range 902.3 sets.",
            clean,
            forbidden_problems=frozenset({"dining_surface_height"}),
            expected_questions=SCAN_CANNOT_SEE | {"reach_range"},
            expected_action="ASK_OWNER",
            max_tier=3,
        ),
        _case(
            "tables_are_bar_height",
            "Every table at 42 inches, which is a bar stool height and no use "
            "from a wheelchair. 226.1 wants one in twenty to work.",
            _tall_tables(),
            expected_problems=frozenset({"dining_surface_height"}),
            expected_questions=SCAN_CANNOT_SEE | {"reach_range"},
            expected_action="ASK_OWNER",
            max_tier=3,
        ),
    ]


def _tall_tables() -> SceneGraph:
    from standardphysics_contracts import Mat4, to_meters

    graph = _clean()
    height = to_meters(42.0)
    for name in ("table_3", "table_4"):
        node = graph.by_id(node_id(name))
        position = node.transform.position
        graph = v.replace(
            graph,
            node.id,
            dimensions=Vec3(x=node.dimensions.x, y=node.dimensions.y, z=height),
            transform=Mat4.translation(position.x, position.y, height / 2),
        )
    return graph


def _lawsuit_cases() -> list[Case]:
    """The counter as Whitaker v. T Rock Inc. describes it, from D-to-C."""
    return [
        _case(
            "lawsuit_counter",
            "A lowered section already exists, and the card reader still sits "
            "on the 47 inch part. 904.4.1 is met; people pay at the high counter.",
            build_lawsuit_graph(),
            build_lawsuit_scenario(),
            expected_problems=frozenset({ROUTE}) | POS_ON_HIGH_COUNTER,
            forbidden_problems=COUNTER_TOO_HIGH,
            expected_inches={"point_of_sale_height": FIXTURE_COUNTER_INCHES},
            expected_action="FIX",
            fix_should_resolve=True,
        ),
    ]


BUILDERS = (
    _aisle_cases,
    _door_cases,
    _counter_cases,
    _lawsuit_cases,
    _coverage_cases,
    _route_shape_cases,
    _blocked_cases,
    _quiet_cases,
    _crowding_cases,
    _tier_2_and_3_cases,
)


def dataset() -> list[Case]:
    cases: list[Case] = []
    for builder in BUILDERS:
        cases.extend(builder())
    _reject_duplicates(cases)
    return cases


def _reject_duplicates(cases: list[Case]) -> None:
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise ValueError(f"two cases share the id {case.id}")
        seen.add(case.id)


def by_id(case_id: str) -> Case:
    for case in dataset():
        if case.id == case_id:
            return case
    raise KeyError(case_id)
