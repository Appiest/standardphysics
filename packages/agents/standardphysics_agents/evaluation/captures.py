"""Real RoomPlan captures, through the same evaluator.

`scenarios.py` builds a room out of knobs, so a slider can move one dimension
while everything else holds still. This is the other end of the same idea: the
room is a capture off a phone, the geometry is whatever the scan found, and
what there is to control is the trip somebody takes through it and where the
furniture stands.

Nothing here is scored. A capture carries no labels, so there is no correct
measurement to read an answer against, and `measurement_error_in` on a room
nobody measured by hand would report the distance to a number that was never
written down. What a capture shows instead is what the checks say about
geometry that was measured rather than authored, and how much of that answer
the scan's own confidence withholds.

Rearranging goes through `fix.moves.apply_moves` and is judged by
`fix.constraints`, the pair the fix agent already uses, so a nudge here can
only be a move the loop was allowed to propose: movable furniture, translated
along the floor, never resized.
"""

from __future__ import annotations

import functools
import json
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable
from uuid import UUID

import standardphysics_fixtures
from standardphysics_contracts import (
    Finding,
    NodeMove,
    Scenario,
    SceneGraph,
    SceneNode,
    Stop,
    Vec3,
    bounds_the_room,
    lies_flat,
    to_inches,
)
from standardphysics_contracts.rules import Tier
from standardphysics_pipeline.footprints import (
    Polygon,
    contains_point,
    floor_polygon,
)
from standardphysics_pipeline.ingest import parse_room_json

from ..assess import Pass, assess
from ..fix.constraints import violations
from ..fix.moves import apply_moves
from ..rules import load_pack
from .configuration import Setup, ledger, measurements, setup

SAMPLES = Path(standardphysics_fixtures.__file__).parent / "data" / "real"
"""The two Apple RoomPlan samples, which ship inside the fixtures package."""


def _checkout_datasets() -> Path:
    """`datasets/` in a source checkout.

    An installed wheel does not carry it, so the captures underneath report
    themselves absent rather than being assumed present.
    """
    return Path(__file__).resolve().parents[4] / "datasets"


@dataclass(frozen=True)
class Capture:
    """One scan on disk, and where it came from."""

    id: str
    name: str
    room_json: Path
    scan_json: Path | None = None

    @property
    def present(self) -> bool:
        return self.room_json.is_file()

    def provenance(self) -> str:
        """The phone and the capture length, read off the scan's own record.

        The Apple samples ship without one, and saying so is better than
        implying we captured them.
        """
        if self.scan_json is None or not self.scan_json.is_file():
            return "Apple RoomPlan sample"
        record = json.loads(self.scan_json.read_text())
        minutes = float(record.get("duration_seconds") or 0.0) / 60.0
        return f"{record.get('device_model', 'a phone')}, {minutes:.0f} min capture"


def _phone(scan: str, name: str) -> Capture:
    folder = _checkout_datasets() / "phone" / scan
    return Capture(scan, name, folder / "room.json", folder / "scan.json")


CAPTURES: tuple[Capture, ...] = (
    Capture("apple_livingroom", "Living room", SAMPLES / "apple_livingroom.room.json"),
    Capture("apple_bedroom3", "Bedroom", SAMPLES / "apple_bedroom3.room.json"),
    _phone("test1", "Seating area"),
    _phone("ravida", "Seating area, second pass"),
)


def available() -> list[Capture]:
    """The captures this checkout actually has."""
    return [capture for capture in CAPTURES if capture.present]


def capture_named(capture_id: str) -> Capture:
    found = next((each for each in CAPTURES if each.id == capture_id), None)
    if found is None:
        raise KeyError(f"no capture named {capture_id}")
    return found


@functools.lru_cache(maxsize=8)
def load(capture_id: str) -> SceneGraph:
    """The capture parsed into a graph, floor at z = 0."""
    capture = capture_named(capture_id)
    if not capture.present:
        raise FileNotFoundError(f"{capture.id} is not in this checkout")
    return parse_room_json(json.loads(capture.room_json.read_text()))


ANCHOR_KINDS = ("door", "opening", "object")
"""What a trip can start or end at. A wall is not a destination."""


def _anchor_order(node: SceneNode) -> tuple:
    """Ways in first, then furniture, each group in a stable order."""
    position = node.transform.position
    return (
        ANCHOR_KINDS.index(node.kind),
        node.label,
        round(position.x, 3),
        round(position.y, 3),
    )


def anchors(graph: SceneGraph) -> dict[str, SceneNode]:
    """Everything a trip could run between, named so a list can tell two
    sofas apart. A label is only numbered when the room holds more than one."""
    found = sorted(
        (node for node in graph.nodes if node.kind in ANCHOR_KINDS), key=_anchor_order
    )
    totals = Counter(node.label for node in found)
    seen: Counter[str] = Counter()
    named: dict[str, SceneNode] = {}
    for node in found:
        seen[node.label] += 1
        suffix = f" {seen[node.label]}" if totals[node.label] > 1 else ""
        named[f"{node.label}{suffix}"] = node
    return named


def movable(graph: SceneGraph) -> dict[str, SceneNode]:
    """The anchors a rearrangement is allowed to touch."""
    return {name: node for name, node in anchors(graph).items() if node.movable}


@dataclass(frozen=True)
class Aim:
    """The capture as the controls leave it.

    Anchors are held by name rather than by UUID so the whole thing stays
    hashable and readable, and so a cache key says which trip it stands for.
    """

    capture: str
    start: str
    end: str
    moved: str | None = None
    shift_x: float = 0.0
    shift_y: float = 0.0
    after_rescan: bool = False
    max_tier: Tier = 1


def _furthest_from(named: dict[str, SceneNode], start: str) -> str:
    """The anchor across the room, so the default trip has to cross it."""
    origin = named[start].transform.position
    return max(
        (name for name in named if name != start),
        key=lambda name: (named[name].transform.position.x - origin.x) ** 2
        + (named[name].transform.position.y - origin.y) ** 2,
    )


def default_aim(capture_id: str) -> Aim:
    """A trip through the room that a scan can answer something about.

    It starts at a way in when the scan found one, because that is where a
    customer starts, and ends at whatever is furthest from it.
    """
    named = anchors(load(capture_id))
    ways_in = [name for name, node in named.items() if bounds_the_room(node)]
    start = ways_in[0] if ways_in else next(iter(named))
    return Aim(capture=capture_id, start=start, end=_furthest_from(named, start))


def after_a_rescan(graph: SceneGraph) -> SceneGraph:
    """The same geometry, with every low-confidence node read as measured.

    RoomPlan reports medium and low confidence on anything it only glimpsed,
    and a check that rests on one of those asks for another look instead of
    ruling. That is the right default and it is also why a real capture comes
    back mostly undecided, so this stands in for the rescan the router would
    ask for: it says what the checks would conclude once the owner had pointed
    the phone at those pieces again.

    It writes `quality` and nothing else. No measurement moves, so this cannot
    manufacture an answer, only stop one from being withheld.
    """
    return graph.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"quality": "measured"})
                if node.quality == "needs_another_look"
                else node
                for node in graph.nodes
            ]
        }
    )


def unsure(graph: SceneGraph) -> list[SceneNode]:
    """The nodes the scan is not confident about."""
    return [node for node in graph.nodes if node.quality == "needs_another_look"]


def _nudge(graph: SceneGraph, aim: Aim) -> SceneGraph:
    if aim.moved is None or (aim.shift_x == 0.0 and aim.shift_y == 0.0):
        return graph
    node = movable(graph).get(aim.moved)
    if node is None:
        raise KeyError(f"{aim.moved} is not a piece this room can move")
    return apply_moves(
        graph,
        [
            NodeMove(
                node_id=node.id,
                delta_translation=Vec3(x=aim.shift_x, y=aim.shift_y, z=0.0),
            )
        ],
    )


def room(aim: Aim) -> SceneGraph:
    """The capture with the controls applied."""
    graph = _nudge(load(aim.capture), aim)
    return after_a_rescan(graph) if aim.after_rescan else graph


def refused(aim: Aim) -> list[str]:
    """Why the fix agent would throw this rearrangement out, if it would.

    A slider does not know where the walls are, so a nudge can push a sofa
    into one. The constraint set that guards a proposal answers that here too,
    which keeps an impossible layout from being reported as a measurement.
    """
    if aim.moved is None:
        return []
    return [
        f"{breach.kind.replace('_', ' ')}: {breach.detail}"
        for breach in violations(load(aim.capture), room(aim))
    ]


def trip(aim: Aim) -> Scenario:
    """The two stops the controls picked, as the scenario the checks walk."""
    named = anchors(room(aim))
    missing = [name for name in (aim.start, aim.end) if name not in named]
    if missing:
        raise KeyError(f"this room has no {' or '.join(missing)}")
    if aim.start == aim.end:
        raise ValueError("a trip needs two different stops")
    return Scenario(
        name=f"{aim.start} to {aim.end}",
        stops=[_stop(name, named[name]) for name in (aim.start, aim.end)],
    )


def _stop(name: str, node: SceneNode) -> Stop:
    position = node.transform.position
    return Stop(
        name=name,
        position=Vec3(x=position.x, y=position.y, z=0.0),
        anchor_node_id=node.id,
    )


def describe(aim: Aim) -> str:
    capture = capture_named(aim.capture)
    moved = (
        ""
        if aim.moved is None or (aim.shift_x == 0.0 and aim.shift_y == 0.0)
        else (
            f", with the {aim.moved.lower()} moved "
            f"{to_inches((aim.shift_x**2 + aim.shift_y**2) ** 0.5):.0f} in"
        )
    )
    return f"{capture.name}, {aim.start.lower()} to {aim.end.lower()}{moved}."


def review(aim: Aim, configuration: Setup | None = None) -> Pass:
    """One capture through `assess`, the function the server runs.

    `run_case` is the wrong door here: it scores, and a capture has no labels
    to score against.
    """
    picked = configuration or setup()
    graph = room(aim)
    return assess(
        graph,
        trip(aim),
        measurements(picked.measurements, picked.cell_size),
        rules=load_pack(),
        ledger=ledger(picked.preview_unverified),
        max_tier=aim.max_tier,
    )


SHIFT_AXES = {"shift_x": "east and west", "shift_y": "north and south"}
"""Which way a nudge runs. The graph is z-up, so the floor is x and y."""


def sweep_shift(
    aim: Aim,
    axis: str,
    metres: Iterable[float],
    configuration: Setup | None = None,
) -> list[tuple[Aim, Pass]]:
    """The same room with one piece at every offset, everything else still."""
    if axis not in SHIFT_AXES:
        raise KeyError(f"{axis} is not a direction a piece can be nudged")
    if aim.moved is None:
        raise ValueError("nothing is selected to move")
    moved = [replace(aim, **{axis: float(value)}) for value in metres]
    return [(each, review(each, configuration)) for each in moved]


def measured(result: Pass, check_id: str) -> float | None:
    """The tightest measurement one check reported, if it reported one."""
    taken = [
        finding.measured_inches
        for finding in result.findings
        if finding.check_id == check_id and finding.measured_inches is not None
    ]
    return min(taken) if taken else None


def withheld(result: Pass) -> dict[str, str]:
    """Checks that measured something and asked for another look anyway.

    This is the shape of a real capture: a number exists, and the scan is not
    sure enough of the geometry behind it to rule. The value is what it asked
    for.
    """
    return {
        finding.check_id: finding.title
        for finding in result.findings
        if finding.outcome == "question" and finding.measured_inches is not None
    }


def walked_path(result: Pass) -> list[Vec3]:
    """The route the checks actually walked, for a plan to trace.

    Whichever check drew it is the one that measured along the whole trip, so
    this asks for the annotation rather than for a check by name.
    """
    for finding in result.findings:
        locus = finding.locus
        if locus and locus.annotation.kind == "path":
            return list(locus.annotation.points)
    return []


def _dimension_ends(finding: Finding, check_id: str) -> tuple[Vec3, Vec3] | None:
    locus = finding.locus
    if finding.check_id != check_id or locus is None:
        return None
    points = locus.annotation.points
    if locus.annotation.kind != "dimension_line" or len(points) != 2:
        return None
    return points[0], points[1]


def pinch_line(result: Pass, check_id: str) -> tuple[Vec3, Vec3] | None:
    """The two points one check measured between, for a plan to mark."""
    for finding in result.findings:
        ends = _dimension_ends(finding, check_id)
        if ends is not None:
            return ends
    return None


def relied_on(result: Pass) -> set[UUID]:
    """Every node some finding rests on, so a plan can show which they were."""
    return {
        node_id
        for finding in result.findings
        if finding.locus
        for node_id in finding.locus.node_ids
    }


def floor_outline(graph: SceneGraph) -> Polygon | None:
    """The scanned floor, as a ground polygon."""
    floor = next((node for node in graph.nodes if lies_flat(node)), None)
    return None if floor is None else floor_polygon(floor)


FLOOR_MARGIN = 0.05
"""Metres of slack when asking whether a step landed on the floor. The grid
quantises at 25 mm, so a step right along the edge is still on it."""


def on_the_floor(graph: SceneGraph, path: list[Vec3]) -> list[bool]:
    """Whether each step of a path landed on the scanned floor.

    `routes.widest_path` keeps a trip inside the room while the scanned floor
    joins its two stops, so a route that left the floor is one the room could
    not answer: a stop standing outside, or two stops the floor cannot connect
    without going around. That is the difference between a route through the
    shop and a walk around the block, so the plan draws the two differently
    rather than presenting both as the trip.
    """
    outline = floor_outline(graph)
    if outline is None:
        return [True] * len(path)
    return [
        contains_point(outline, (step.x, step.y), FLOOR_MARGIN) for step in path
    ]


def strayed(graph: SceneGraph, path: list[Vec3]) -> float:
    """What share of the walked path left the scanned floor."""
    if not path:
        return 0.0
    inside = on_the_floor(graph, path)
    return sum(0 if each else 1 for each in inside) / len(path)


def implicated(result: Pass, graph: SceneGraph) -> list[str]:
    """The movable pieces this trip's answers rest on.

    Moving anything else changes the layout and not the reading, so a list
    that puts these first is offering the nudges that can actually move a
    measurement.
    """
    answers_rest_on = relied_on(result)
    return [
        name for name, node in movable(graph).items() if node.id in answers_rest_on
    ]


__all__ = [
    "ANCHOR_KINDS", "CAPTURES", "FLOOR_MARGIN", "SAMPLES", "SHIFT_AXES",
    "Aim", "Capture", "after_a_rescan", "anchors", "available",
    "capture_named", "default_aim", "describe", "floor_outline", "implicated",
    "load", "measured", "movable", "on_the_floor", "pinch_line", "refused",
    "relied_on", "review", "room", "strayed", "sweep_shift", "trip", "unsure",
    "walked_path", "withheld",
]
