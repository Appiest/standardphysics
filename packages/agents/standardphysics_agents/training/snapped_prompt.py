"""The room as a model sees it in training: compact JSON and one instruction.

This is the prompt the Fireworks rearrangement model was trained on, kept byte for byte
(`test_fireworks_prompt_stays_byte_for_byte_as_trained`). `prompt.py` is the one the menu
and the owner's model loop read.

The instruction and the answer schema come from `redesign.py`. The room keeps
only what a rearrangement needs: walls as segments, doors, fixed obstacles,
movable furniture with its size, heading and the way its front faces, the route
stops, the furniture-fixable problems the checker measured, the seats facing
away from what they serve, the room's usefulness shares, and the ADA layout
directives for the space type when one is known.
"""

from __future__ import annotations

import json
import math

from standardphysics_contracts import Finding, Scenario, SceneGraph, SceneNode, bounds_the_room, lies_flat
from standardphysics_pipeline.footprints import floor_polygon, rotation_about_z
from standardphysics_pipeline.occupancy import blocks_floor

from ..fix.constraints import MAX_TRAVEL_METERS, interior_bounds
from ..fix.moves import measured_position
from ..precedents import PrecedentCompiler
from ..redesign import INSTRUCTION
from ..snap import facing_error_degrees
from .checker import TrainingChecker
from .edits import yaw_degrees
from .usefulness import FACING_TOLERANCE_DEGREES, usefulness

ANSWER_FORMAT = (
    'Answer with JSON only, no prose: {"moves":[{"node_id":"<id from movable_objects>","dx":<meters>,'
    '"dy":<meters>,"rotation_degrees":<degrees>}]}. dx and dy slide the object across the floor from where it '
    "is now; rotation_degrees turns it about its own centre. A piece may end at most "
    f"{MAX_TRAVEL_METERS:.2f} m from where the scan found it (`travel_left_m` says how much it has left). "
    "Clear every problem in `problems` if you can, move as little as possible, and keep every table and seat "
    "usable. Moves are requests: each piece lands on the nearest spot the room allows, so aim where you want it "
    "and keep requests close to legal, because every metre the application has to shift a request costs you. "
    "`front_degrees` is the direction a piece faces. A seat you move turns to face the table, desk or counter it "
    "sits at, and shelving, fridges and stations turn their backs to the wall; a seat in `facing_away` has to be "
    "moved or turned to face what it serves. Do not trade away `usefulness`: seats facing what they serve, clear "
    "floor in front of wall pieces, and enough accessible tables. When `ada_layout_constraints` is present, "
    "every one of them has to hold."
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
    cos_t, sin_t = rotation_about_z(node)
    centre, half = node.transform.position, node.dimensions.x / 2
    return [_r(centre.x - cos_t * half), _r(centre.y - sin_t * half),
            _r(centre.x + cos_t * half), _r(centre.y + sin_t * half)]


def _placed(node: SceneNode) -> dict:
    centre = node.transform.position
    return {
        "label": node.label,
        "center": [_r(centre.x), _r(centre.y)],
        "size": [_r(node.dimensions.x), _r(node.dimensions.y), _r(node.dimensions.z)],
        "heading_degrees": round(yaw_degrees(node)),
        "front_degrees": round((yaw_degrees(node) - 90.0 + 180.0) % 360.0 - 180.0),
    }


def _facing_away(graph: SceneGraph) -> list[dict]:
    wrong = []
    for node in graph.nodes:
        if not node.movable or node.kind != "object":
            continue
        error = facing_error_degrees(node, graph)
        if error is not None and error > FACING_TOLERANCE_DEGREES:
            wrong.append({"id": str(node.id), "label": node.label, "off_by_degrees": round(error)})
    return wrong


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
    fixed = [node for node in graph.nodes if not node.movable and not bounds_the_room(node) and blocks_floor(node)]
    bounds = interior_bounds(graph)
    return {
        "units": "metres and degrees; x and y lie on the floor",
        "floor_inside_walls": None if bounds is None else [_r(value) for value in bounds],
        "walls": [_wall(node) for node in graph.nodes if node.kind == "wall" and not lies_flat(node)],
        "doors": [_placed(node) for node in graph.nodes if node.kind == "door"],
        "fixed_objects": [_placed(node) for node in fixed],
        "movable_objects": [_movable(node) for node in graph.nodes if node.movable and not bounds_the_room(node)],
        "route_stops": [
            {"name": stop.name, "at": [_r(stop.position.x), _r(stop.position.y)]} for stop in scenario.stops
        ],
        "problems": [_problem(finding, graph) for finding in problems],
        "facing_away": _facing_away(graph),
        "usefulness": usefulness(graph).as_dict(),
    }


def prompt_messages(graph: SceneGraph, checker: TrainingChecker) -> list[dict]:
    problems = checker.fixable_problems(checker.assess(graph))
    view = room_view(graph, checker.scenario, problems)
    directives = checker.directives_for(graph)
    if directives:
        view["ada_layout_constraints"] = PrecedentCompiler(directives).format_qwen_precedent_prompt(directives)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(view, separators=(",", ":"))},
    ]


def openrouter_prompt_messages(graph: SceneGraph, checker: TrainingChecker) -> list[dict]:
    """Give the general model the actual floor polygon checked by constraints."""
    messages = prompt_messages(graph, checker)
    view = json.loads(messages[1]["content"])
    floor = next((node for node in graph.nodes if lies_flat(node)), None)
    view.pop("floor_inside_walls", None)
    view["floor_polygon"] = floor_polygon(floor) if floor is not None else None
    messages[1]["content"] = json.dumps(view, separators=(",", ":"))
    return messages
