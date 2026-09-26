"""The room as a model sees it in training: compact JSON and one instruction.

The instruction and the answer schema come from `redesign.py`. The room keeps
only what a rearrangement needs: walls as segments, doors with the floor their
swing keeps clear, fixed obstacles including what stands on counters, movable
furniture, the route stops, and the furniture-fixable problems the checker
measured. Every piece carries its footprint corners, so the model never has to
turn a centre, size and heading into an outline itself.
"""

from __future__ import annotations

import json
import math

from standardphysics_contracts import Finding, Scenario, SceneGraph, SceneNode, bounds_the_room, lies_flat
from standardphysics_pipeline.footprints import footprint, rotation_about_z
from standardphysics_pipeline.occupancy import blocks_floor

from ..fix.constraints import MAX_TRAVEL_METERS, door_keep_clear, interior_bounds, on_a_surface
from ..fix.moves import measured_position
from ..redesign import INSTRUCTION
from .checker import TrainingChecker
from .construction import MAX_FIXTURE_MOVE_INCHES, MAX_WALL_SHIFT_INCHES, fixture_ids, floor_edges
from .edits import yaw_degrees

ANSWER_FORMAT = (
    'Answer with JSON only, no prose: {"moves":[{"node_id":"<id from movable_objects>","dx":<meters>,'
    '"dy":<meters>,"rotation_degrees":<degrees>}]}. dx and dy slide the object across the floor from where it '
    "is now; rotation_degrees turns it about its own centre. A piece may end at most "
    f"{MAX_TRAVEL_METERS:.2f} m from where the scan found it (`travel_left_m` says how much it has left). "
    "Clear every problem in `problems` if you can, move as little as possible, keep every table and seat "
    "usable, and never push anything into a wall, a door's `keep_clear` area or another object, including the "
    "things standing on counters. `corners` is each piece's outline on the floor. Only when furniture alone "
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
        "corners": _corners(footprint(node)),
    }


def _corners(polygon) -> list[list[float]]:
    return [[_r(x), _r(y)] for x, y in polygon]


def _door(node: SceneNode) -> dict:
    return {**_placed(node), "keep_clear": _corners(door_keep_clear(node))}


def _fixed(node: SceneNode, fixtures: set, on_counters: set) -> dict:
    entry = {"id": str(node.id), **_placed(node)} if node.id in fixtures else _placed(node)
    return {**entry, "on_a_counter": True} if node.id in on_counters else entry


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


def room_view(graph: SceneGraph, scenario: Scenario, problems: list[Finding]) -> dict:
    on_counters = on_a_surface(graph)
    fixed = [node for node in graph.nodes if not node.movable and not bounds_the_room(node)
             and (blocks_floor(node) or node.id in on_counters)]
    fixtures = fixture_ids(graph)
    bounds = interior_bounds(graph)
    return {
        "units": "metres and degrees; x and y lie on the floor",
        "floor_inside_walls": None if bounds is None else [_r(value) for value in bounds],
        "walls": [_wall(node) for node in graph.nodes if node.kind == "wall" and not lies_flat(node)],
        "doors": [_door(node) for node in graph.nodes if node.kind == "door"],
        "fixed_objects": [_fixed(node, fixtures, on_counters) for node in fixed],
        "movable_objects": [_movable(node) for node in graph.nodes if node.movable and not bounds_the_room(node)],
        "route_stops": [
            {"name": stop.name, "at": [_r(stop.position.x), _r(stop.position.y)]} for stop in scenario.stops
        ],
        "problems": [_problem(finding, graph) for finding in problems],
        "walls_you_can_move": [
            {"side": edge.side, "outward": [_r(edge.outward[0]), _r(edge.outward[1])], "edge": edge.segment()}
            for edge in floor_edges(graph)
        ],
    }


def prompt_messages(graph: SceneGraph, checker: TrainingChecker) -> list[dict]:
    problems = checker.fixable_problems(checker.assess(graph))
    view = room_view(graph, checker.scenario, problems)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(view, separators=(",", ":"))},
    ]
