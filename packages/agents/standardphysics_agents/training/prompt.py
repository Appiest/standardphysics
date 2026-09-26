"""The room as a model sees it in training: compact JSON and one instruction.

The instruction and the answer schema come from `redesign.py`. The room keeps
only what a rearrangement needs: walls as segments, doors, fixed obstacles,
movable furniture with its size and heading, the route stops, and the
furniture-fixable problems the checker measured. In the fittings scope the room
also lists how high each counter, table and mounted item is, the ends of each
service counter with the registers on it, and the system prompt adds the
catalog and the fitting edits.
"""

from __future__ import annotations

import json
import math

from standardphysics_contracts import Finding, Scenario, SceneGraph, SceneNode, bounds_the_room, to_inches
from standardphysics_pipeline import footprint, gap_between
from standardphysics_pipeline.footprints import rotation_about_z
from standardphysics_pipeline.occupancy import blocks_floor

from ..checks import roles
from ..checks.walls import standing_walls
from ..fix.constraints import MAX_TRAVEL_METERS, interior_bounds
from ..fix.moves import measured_position
from ..redesign import INSTRUCTION
from .catalog import CATALOG
from .checker import Scope, TrainingChecker
from .construction import MAX_FIXTURE_MOVE_INCHES, MAX_WALL_SHIFT_INCHES, fixture_ids, floor_edges
from .edits import yaw_degrees
from .fittings import MAX_SECTION_INCHES, MIN_SECTION_INCHES, height_range, use_of

ANSWER_FORMAT = (
    'Answer with JSON only, no prose: {"moves":[{"node_id":"<id from movable_objects>","dx":<meters>,'
    '"dy":<meters>,"rotation_degrees":<degrees>}]}. dx and dy slide the object across the floor from where it '
    "is now; rotation_degrees turns it about its own centre. A piece may end at most "
    f"{MAX_TRAVEL_METERS:.2f} m from where the scan found it (`travel_left_m` says how much it has left). "
    "Clear every problem in `problems` if you can, move as little as possible, keep every table and seat "
    "usable, and never push anything into a wall, a door swing or another object. Only when furniture alone "
    'cannot clear a problem, you may also add "wall_shifts":[{"side":"<side from walls_you_can_move>",'
    f'"inches":<1 to {MAX_WALL_SHIFT_INCHES:.0f}>}}] to push that side of the room outward, or '
    '"fixture_moves":[{"node_id":"<id from fixed_objects>","dx_inches":<inches>,"dy_inches":<inches>}] '
    f"(at most {MAX_FIXTURE_MOVE_INCHES:.0f} in each way) to relocate a built-in fixture; both are construction, "
    "so use the fewest inches that work."
)

TRAINING_INSTRUCTION = INSTRUCTION.split(" Use `actionable_failures`")[0] + (
    " `problems` lists the measured problems furniture can address, each naming the objects involved. "
    "Use only IDs in `movable_objects`. Preserve every object's measured size, inventory, fixed fixtures, walls "
    "and doors. Do not return a no-op move. The application remeasures every route and rule before accepting edits."
)

SYSTEM_PROMPT = f"{TRAINING_INSTRUCTION}\n\n{ANSWER_FORMAT}"

FITTINGS_INSTRUCTION = TRAINING_INSTRUCTION.replace(
    "the measured problems furniture can address",
    "the measured problems furniture or the fitting edits below can address",
)

FITTINGS_FORMAT = (
    " Some problems are about what a piece is rather than where it stands: a counter too high to be served at, "
    "too few tables at a height a wheelchair user can sit at, a control mounted out of reach. For those you may add "
    '"height_changes":[{"node_id":"<id from heights>","top_inches":<inches within its top_range_inches>}] to '
    'rebuild a counter or table, or rehang a mounted item, with a new top; '
    '"replacements":[{"node_id":"<id from heights>","catalog_item":"<name from its can_replace_with>"}] to swap '
    "a piece for a catalog one in the same place; or "
    '"add_lowered_section":[{"counter_id":"<id from counters>","end":"start"|"end","length_inches":'
    f'<{MIN_SECTION_INCHES:.0f} to {MAX_SECTION_INCHES:.0f}>,"carry":["<id from that counter\'s point_of_sale>"]}}] '
    "to cut a 36 in high section into that end of the counter and set the registers or card readers in `carry` "
    "down on it, which is where people have to be able to pay. Keep the floor in front of a new section clear "
    "30 by 48 in. Catalog: "
    + json.dumps([item.as_prompt() for item in CATALOG.values()], separators=(",", ":"))
    + ". Prefer the cheapest fix that works: carrying a register costs least, then moving furniture, then a "
    "height change, replacement or new section, then a fixture move, and a wall shift costs most."
)

FITTINGS_SYSTEM_PROMPT = f"{FITTINGS_INSTRUCTION}\n\n{ANSWER_FORMAT}{FITTINGS_FORMAT}"

SYSTEM_PROMPTS: dict[Scope, str] = {"layout": SYSTEM_PROMPT, "fittings": FITTINGS_SYSTEM_PROMPT}


def _r(value: float) -> float:
    return round(value, 2)


def _wall(node: SceneNode) -> list[float]:
    """The wall's centre line, along whichever horizontal extent is its length."""
    cos_t, sin_t = rotation_about_z(node)
    if node.dimensions.y > node.dimensions.x:
        cos_t, sin_t = -sin_t, cos_t
    centre, half = node.transform.position, max(node.dimensions.x, node.dimensions.y) / 2
    return [_r(centre.x - cos_t * half), _r(centre.y - sin_t * half),
            _r(centre.x + cos_t * half), _r(centre.y + sin_t * half)]


def _placed(node: SceneNode) -> dict:
    centre = node.transform.position
    return {
        "label": node.label,
        "center": [_r(centre.x), _r(centre.y)],
        "size": [_r(node.dimensions.x), _r(node.dimensions.y), _r(node.dimensions.z)],
        "heading_degrees": round(yaw_degrees(node)),
    }


def _movable(node: SceneNode) -> dict:
    origin, now = measured_position(node), node.transform.position
    travelled = math.hypot(now.x - origin.x, now.y - origin.y)
    return {"id": str(node.id), **_placed(node), "travel_left_m": _r(max(0.0, MAX_TRAVEL_METERS - travelled))}


def _problem(finding: Finding, graph: SceneGraph) -> dict:
    labels = {node.id: node.label for node in graph.nodes}
    locus = finding.locus
    return {
        "check": finding.check_id,
        "title": finding.title,
        "measured_inches": None if finding.measured_inches is None else round(finding.measured_inches, 1),
        "required_inches": finding.required_inches,
        "at": None if locus is None or locus.point is None else [_r(locus.point.x), _r(locus.point.y)],
        "involves": [] if locus is None else [
            {"id": str(node_id), "label": labels.get(node_id, "?")} for node_id in locus.node_ids
        ],
    }


def _top_inches(node: SceneNode) -> float:
    return round(to_inches(node.transform.position.z + node.dimensions.z / 2), 1)


def _replacements_for(graph: SceneGraph, node: SceneNode) -> list[str]:
    use = use_of(graph, node)
    serves = {"counter": True, "surface": False}.get(use)
    return [] if serves is None else [item.name for item in CATALOG.values()
                                      if (item.knee_clearance_inches is None) == serves]


def _heights(graph: SceneGraph) -> list[dict]:
    found = []
    for node in graph.nodes:
        allowed = height_range(graph, node)
        if allowed is None:
            continue
        centre = node.transform.position
        found.append({"id": str(node.id), "label": node.label, "center": [_r(centre.x), _r(centre.y)],
                      "top_inches": _top_inches(node), "top_range_inches": list(allowed),
                      "can_replace_with": _replacements_for(graph, node)})
    return found


def _counter_ends(node: SceneNode) -> dict:
    x1, y1, x2, y2 = _wall(node)
    return {"start": [x1, y1], "end": [x2, y2]}


def _counters(graph: SceneGraph) -> list[dict]:
    sellers = roles.point_of_sale(graph)
    return [{"id": str(counter.id), "label": counter.label, "top_inches": _top_inches(counter), **_counter_ends(counter),
             "point_of_sale": [{"id": str(item.id), "label": item.label,
                                "at": [_r(item.transform.position.x), _r(item.transform.position.y)]}
                               for item in sellers if gap_between(footprint(item), footprint(counter)) == 0.0]}
            for counter in roles.service_counters(graph)]


def fittings_view(graph: SceneGraph) -> dict:
    return {"heights": _heights(graph), "counters": _counters(graph)}


def room_view(graph: SceneGraph, scenario: Scenario, problems: list[Finding], scope: Scope = "layout") -> dict:
    fixed = [node for node in graph.nodes if not node.movable and not bounds_the_room(node) and blocks_floor(node)]
    fixtures = fixture_ids(graph)
    bounds = interior_bounds(graph)
    return {
        "units": "metres and degrees; x and y lie on the floor",
        "floor_inside_walls": None if bounds is None else [_r(value) for value in bounds],
        "walls": [_wall(node) for node in standing_walls(graph)],
        "doors": [_placed(node) for node in roles.doors(graph)],
        "fixed_objects": [{"id": str(node.id), **_placed(node)} if node.id in fixtures else _placed(node)
                          for node in fixed],
        "movable_objects": [_movable(node) for node in graph.nodes if node.movable and not bounds_the_room(node)],
        "route_stops": [
            {"name": stop.name, "at": [_r(stop.position.x), _r(stop.position.y)]} for stop in scenario.stops
        ],
        "problems": [_problem(finding, graph) for finding in problems],
        "walls_you_can_move": [
            {"side": edge.side, "outward": [_r(edge.outward[0]), _r(edge.outward[1])], "edge": edge.segment()}
            for edge in floor_edges(graph)
        ],
        **(fittings_view(graph) if scope == "fittings" else {}),
    }


def prompt_messages(graph: SceneGraph, checker: TrainingChecker) -> list[dict]:
    problems = checker.fixable_problems(checker.assess(graph))
    view = room_view(graph, checker.scenario, problems, checker.scope)
    return [
        {"role": "system", "content": SYSTEM_PROMPTS[checker.scope]},
        {"role": "user", "content": json.dumps(view, separators=(",", ":"))},
    ]
