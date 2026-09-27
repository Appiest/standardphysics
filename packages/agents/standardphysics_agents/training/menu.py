"""A menu of legal moves: the model picks from options code has already checked and measured.

Asked for raw `{"moves":[{"node_id","dx","dy","rotation_degrees"}]}`, the base
model had 57-77% of its answers refused for collisions or leaving the floor on
square rooms, and on rotated rooms it mostly answered with no moves at all.
Models that write coordinates fail this way; systems that never place anything
illegally let code own the geometry. So the model here chooses, and code places.

1. Generate. For each fixable problem, the solver's own guesses: the slide
   ladder of `fix/strategies.py` and the placement beam of `fix/placement.py`,
   plus short slides of a built-in fixture the problem names. Each is worded as
   a relation ("slide Chair [3f2a] 14 in away from the Cafe table, for P1").
   After Holodeck (Yang et al., CVPR 2024, arXiv:2312.09067), where the language
   model states relations and a solver enforces no-collision and in-bounds.
2. Mask. Only guesses `reward.constrained` finds nothing wrong with survive:
   `fix.violations`, and `relocation_violations` for a relocated fixture, the
   same test that refuses an answer. An illegal move can never be offered.
   After invalid action masking (Huang & Ontanon, FLAIRS 2022, arXiv:2006.14171).
3. Measure. The training checker runs on every survivor, and each option is
   labelled with what it clears, what it improves (before -> after inches),
   any new problem, usability, and inches moved. Options the gate would refuse
   are left off. After SayCan (Ahn et al., 2022, arXiv:2204.01691), which
   combines the language model's choice with a feasibility score.
4. Resolve. The model answers `{"choose":[3,7],"why":"..."}`. Picks apply in
   order, and a pick that has become illegal after earlier ones, or moves a
   piece an earlier pick already moved, is dropped and reported, so the room
   applied is always legal. A free-form `{"moves":[...]}` answer is snapped to
   legal floor by `fix/snap.py` instead of being refused.

Each turn is stateless: the prompt is the current room, the menu and the last
result, never the whole conversation.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from itertools import chain, zip_longest
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from standardphysics_contracts import Finding, NodeMove, SceneGraph, SceneNode, to_inches, to_meters

from ..evaluation.gate import accepts
from ..fix import candidates, describe, pinch_from, snap_moves
from ..fix.placement import placements
from ..redesign import FurnitureMove
from .checker import TrainingChecker
from .construction import MAX_FIXTURE_MOVE_INCHES, FixtureMove, build, construction_inches, fixture_ids
from .edits import TrainingEdits, _json_text, edits_json, node_moves, parse_edits
from .prompt import room_view
from .reward import constrained
from .usability import usability

GUESSES_PER_PROBLEM = 24
PLACEMENTS_PER_PROBLEM = 24
FIXTURE_STEPS_INCHES = (6.0, 12.0, MAX_FIXTURE_MOVE_INCHES)
FIXTURE_DIRECTIONS = tuple((math.cos(math.radians(angle)), math.sin(math.radians(angle)))
                           for angle in range(0, 360, 45))
FURNITURE_TRIES = 12
FIXTURE_TRIES = 6
"""Legal guesses measured per problem; each costs one full checker pass.

Fixture slides are construction, so they are only measured for a problem no
furniture option clears.
"""
OPTIONS_PER_PROBLEM = 4
MENU_SIZE = 12
MAX_PICKS = MENU_SIZE
TURN_WORDING_DEGREES = 1.0

MENU_INSTRUCTION = (
    "You are rearranging a shop so a wheelchair user can get around it. `problems` lists the measured "
    "accessibility problems furniture can address, each with a label such as P1. `options` lists moves that "
    "have already been checked: every one is legal (no collisions, stays on the floor, keeps doors clear) and "
    "was measured on its own, so its `clears`, `improves`, `usable` and `inches_moved` are facts, not guesses. "
    "Pick the options that together clear the most problems with the least disruption. Options apply in the "
    "order you list them; one that would clash with an earlier pick or moves the same piece again is skipped "
    "and reported to you, and `clashes_with` names the options each one cannot be combined with. "
    "`last_result` says what happened to your previous answer, if any."
)
MENU_ANSWER_FORMAT = (
    'Answer with JSON only: {"choose":[<option numbers in the order to apply>],"why":"<one sentence>"}. '
    'Only if no option helps, you may instead answer {"moves":[{"node_id":"<id>","dx":<meters>,"dy":<meters>,'
    '"rotation_degrees":<degrees>}]} using ids from `room.movable_objects`; each such move is moved to the '
    "nearest legal spot, or dropped if there is none."
)
MENU_SYSTEM_PROMPT = f"{MENU_INSTRUCTION}\n\n{MENU_ANSWER_FORMAT}"


@dataclass(frozen=True)
class Option:
    number: int
    wording: str
    edits: TrainingEdits
    effect: dict

    def as_prompt(self) -> dict:
        return {"option": self.number, "do": self.wording, **self.effect}


@dataclass(frozen=True)
class Menu:
    problems: dict[UUID, str]
    """Each current fixable problem's finding id, and the label the model reads it by."""
    options: list[Option]
    problem_view: list[dict] = field(default_factory=list)

    def option(self, number: int) -> Option | None:
        return next((option for option in self.options if option.number == number), None)


@dataclass(frozen=True)
class _Guess:
    edits: TrainingEdits
    wording: str


def _name(node: SceneNode) -> str:
    return f"{node.label} [{str(node.id)[:4]}]"


def _anchor(graph: SceneGraph, node: SceneNode, finding: Finding) -> tuple[str, tuple[float, float]]:
    """What a move is described relative to: the nearest other piece the problem names, or the problem's spot."""
    here = (node.transform.position.x, node.transform.position.y)
    nodes = {other.id: other for other in graph.nodes}
    others = [nodes[node_id] for node_id in (finding.locus.node_ids if finding.locus else [])
              if node_id != node.id and node_id in nodes]
    if others:
        other = min(others, key=lambda o: math.dist(here, (o.transform.position.x, o.transform.position.y)))
        return f"the {other.label}", (other.transform.position.x, other.transform.position.y)
    point = finding.locus.point if finding.locus and finding.locus.point else node.transform.position
    return f"the {finding.check_id.replace('_', ' ')}", (point.x, point.y)


def _turn_words(degrees: float) -> str:
    return f"{abs(degrees):.0f} degrees {'counter-clockwise' if degrees > 0 else 'clockwise'}"


def _slide_words(graph, node, finding, dx: float, dy: float, verb: str) -> str:
    anchor, spot = _anchor(graph, node, finding)
    here = (node.transform.position.x, node.transform.position.y)
    away = math.dist((here[0] + dx, here[1] + dy), spot) >= math.dist(here, spot)
    inches = to_inches(math.hypot(dx, dy))
    return f"{verb} {_name(node)} {inches:.0f} in {'further from' if away else 'closer to'} {anchor}"


def _move_words(graph: SceneGraph, move: NodeMove, finding: Finding) -> str:
    node = graph.by_id(move.node_id)
    delta, degrees = move.delta_translation, move.delta_rotation_z_degrees
    turned = abs(degrees) >= TURN_WORDING_DEGREES
    if math.hypot(delta.x, delta.y) < to_meters(0.5):
        return f"turn {_name(node)} {_turn_words(degrees)}"
    words = _slide_words(graph, node, finding, delta.x, delta.y, "slide")
    return f"{words} and turn it {_turn_words(degrees)}" if turned else words


def _furniture_guesses(graph: SceneGraph, finding: Finding, checker: TrainingChecker, label: str) -> list[_Guess]:
    pinch = pinch_from(finding, graph)
    if pinch is None or not pinch.fixable:
        return []
    found = [*candidates(pinch, GUESSES_PER_PROBLEM),
             *placements(graph, pinch, finding, checker.rules, PLACEMENTS_PER_PROBLEM)]
    found.sort(key=lambda candidate: candidate.disruption)
    return _varied([_Guess(TrainingEdits(moves=[_furniture(move) for move in candidate.moves]),
                   "; ".join(_move_words(graph, move, finding) for move in candidate.moves) + f", for {label}")
            for candidate in found if not any(move.node_id in checker.pinned for move in candidate.moves)])


def _varied(guesses: list[_Guess]) -> list[_Guess]:
    """The least disruptive guess for each set of pieces first, then the next of each, and so on.

    A region blocked by two chairs clears only when both move, and sorted by
    disruption alone every single-chair slide would use up the tries first.
    """
    by_pieces: dict[frozenset, list[_Guess]] = {}
    for guess in guesses:
        by_pieces.setdefault(frozenset(_touched(guess.edits)), []).append(guess)
    return [guess for guess in chain.from_iterable(zip_longest(*by_pieces.values())) if guess is not None]


def _furniture(move: NodeMove) -> FurnitureMove:
    return FurnitureMove(node_id=move.node_id, dx=move.delta_translation.x, dy=move.delta_translation.y,
                         rotation_degrees=move.delta_rotation_z_degrees)


def _fixture_guesses(graph: SceneGraph, finding: Finding, checker: TrainingChecker, label: str) -> list[_Guess]:
    """Short slides of a built-in the problem names, in eight directions; construction, so offered last."""
    fixtures = fixture_ids(graph) - set(checker.pinned)
    named = [node_id for node_id in (finding.locus.node_ids if finding.locus else []) if node_id in fixtures]
    found = []
    for node_id in named:
        node = graph.by_id(node_id)
        for inches in FIXTURE_STEPS_INCHES:
            for x, y in FIXTURE_DIRECTIONS:
                move = FixtureMove(node_id=node_id, dx_inches=round(x * inches, 1), dy_inches=round(y * inches, 1))
                words = _slide_words(graph, node, finding, to_meters(move.dx_inches), to_meters(move.dy_inches),
                                     "move built-in")
                found.append(_Guess(TrainingEdits(fixture_moves=[move]), f"{words} (construction), for {label}"))
    return found


def _legal(room: SceneGraph, edits: TrainingEdits) -> SceneGraph | None:
    """The room these edits make, or None when any hard constraint breaks."""
    try:
        candidate, broken = constrained(room, edits)
    except ValueError:
        return None
    return None if broken else candidate


def _effect(room, candidate, checker, before, after, problems: dict[UUID, str], edits: TrainingEdits) -> dict:
    remaining = {finding.id: finding for finding in after.problems}
    improves = [
        {"problem": label, "from_inches": _inches(finding), "to_inches": _inches(remaining[finding_id])}
        for finding_id, label in problems.items()
        for finding in before.problems if finding.id == finding_id and finding_id in remaining
        and _inches(remaining[finding_id]) != _inches(finding)
    ]
    known = {finding.id for finding in before.problems}
    owner = checker.owner_layout or room
    return {
        "clears": [label for finding_id, label in problems.items() if finding_id not in remaining],
        "improves": improves,
        "new_problems": [finding.title for finding in after.problems if finding.id not in known],
        "fixable_left": len(checker.fixable_problems(after)),
        "usable": round(usability(room, candidate, owner, checker.scenario), 3),
        "inches_moved": round(to_inches(sum(math.hypot(move.dx, move.dy) for move in edits.moves)), 1),
        "construction_inches": round(construction_inches(edits.wall_shifts, edits.fixture_moves), 1),
    }


def _inches(finding: Finding) -> float | None:
    return None if finding.measured_inches is None else round(finding.measured_inches, 1)


def _rank(effect: dict) -> tuple:
    return (-len(effect["clears"]), effect["fixable_left"], effect["construction_inches"], -effect["usable"],
            effect["inches_moved"])


def _problem_view(graph: SceneGraph, problems: list[Finding], labels: dict[UUID, str]) -> list[dict]:
    names = {node.id: _name(node) for node in graph.nodes}
    return [{"label": labels[finding.id], "check": finding.check_id, "title": finding.title,
             "measured_inches": _inches(finding), "required_inches": finding.required_inches,
             "involves": [names.get(node_id, "?") for node_id in (finding.locus.node_ids if finding.locus else [])]}
            for finding in problems]


@dataclass
class _Measurer:
    """Legal guesses measured against one room, each worded differently from every option already kept."""

    room: SceneGraph
    checker: TrainingChecker
    before: object
    labels: dict[UUID, str]
    worded: set = field(default_factory=set)

    def options(self, guesses: list[_Guess], tries: int) -> list[tuple[_Guess, dict]]:
        found: list[tuple[_Guess, dict]] = []
        for guess in guesses:
            if tries == 0 or len(found) == OPTIONS_PER_PROBLEM:
                break
            candidate = None if guess.wording in self.worded else _legal(self.room, guess.edits)
            if candidate is None:
                continue
            tries -= 1
            after = self.checker.assess(candidate)
            if accepts(self.before, after):
                self.worded.add(guess.wording)
                found.append((guess, _effect(self.room, candidate, self.checker, self.before, after, self.labels,
                                             guess.edits)))
        return found

    def for_problem(self, finding: Finding) -> list[tuple[_Guess, dict]]:
        label = self.labels[finding.id]
        found = self.options(_furniture_guesses(self.room, finding, self.checker, label), FURNITURE_TRIES)
        if any(label in effect["clears"] for _, effect in found):
            return found
        return [*found, *self.options(_fixture_guesses(self.room, finding, self.checker, label), FIXTURE_TRIES)]


def build_menu(room: SceneGraph, checker: TrainingChecker) -> Menu:
    """Legal, gate-accepted options for each of the room's fixable problems, best first, numbered from 1."""
    before = checker.assess(room)
    problems = checker.fixable_problems(before)
    labels = {finding.id: f"P{index}" for index, finding in enumerate(problems, start=1)}
    measurer = _Measurer(room, checker, before, labels)
    measured = [pair for finding in problems for pair in measurer.for_problem(finding)]
    measured.sort(key=lambda pair: _rank(pair[1]))
    options = [Option(number, guess.wording, guess.edits, effect)
               for number, (guess, effect) in enumerate(measured[:MENU_SIZE], start=1)]
    for option in options:
        option.effect["clashes_with"] = [other.number for other in options if other is not option
                                         and _why_dropped(room, option.edits, other)]
    return Menu(problems=labels, options=options, problem_view=_problem_view(room, problems, labels))


def menu_messages(room: SceneGraph, checker: TrainingChecker, menu: Menu, last_result: dict | None) -> list[dict]:
    """System, then one user message: the current room, its problems, the menu and the last result."""
    view = room_view(room, checker.scenario, [])
    view.pop("problems", None)
    view.pop("walls_you_can_move", None)
    content = {"problems": menu.problem_view, "options": [option.as_prompt() for option in menu.options],
               "last_result": last_result, "room": view}
    return [{"role": "system", "content": MENU_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(content, separators=(",", ":"))}]


class MenuChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")
    choose: list[int] = Field(max_length=MAX_PICKS)
    why: str = ""


def parse_choice(reply: str) -> MenuChoice | None:
    try:
        return MenuChoice.model_validate(json.loads(_json_text(reply)))
    except (ValueError, ValidationError):
        return None


@dataclass
class Resolution:
    """What a reply became: the edits JSON the checker scores, and what was applied, dropped or snapped."""

    completion: str
    interface: str
    picks: list[int] = field(default_factory=list)
    applied: list[int] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    why: str = ""
    snapped: dict | None = None

    def as_dict(self) -> dict:
        return {"interface": self.interface, "picks": self.picks, "applied": self.applied,
                "dropped": self.dropped, "why": self.why, "snapped": self.snapped}


EMPTY = TrainingEdits()


def _combined(applied: TrainingEdits, option: TrainingEdits) -> TrainingEdits:
    return TrainingEdits(moves=[*applied.moves, *option.moves], fixture_moves=[*applied.fixture_moves,
                                                                              *option.fixture_moves])


def _touched(edits: TrainingEdits) -> set[UUID]:
    return {move.node_id for move in edits.moves} | {move.node_id for move in edits.fixture_moves}


def _why_dropped(room: SceneGraph, applied: TrainingEdits, option: Option | None) -> str | None:
    if option is None:
        return "no such option"
    if _touched(applied) & _touched(option.edits):
        return "moves a piece an earlier pick already moved"
    try:
        _, broken = constrained(room, _combined(applied, option.edits))
    except ValueError:
        return "unbuildable construction"
    return describe(broken[0]) if broken else None


def resolve_choice(room: SceneGraph, menu: Menu, choice: MenuChoice) -> Resolution:
    """Picks applied in order; any that became illegal after earlier ones is dropped and reported."""
    resolution = Resolution(completion="", interface="choose", picks=list(choice.choose), why=choice.why)
    applied = EMPTY
    for number in choice.choose:
        option = menu.option(number)
        reason = _why_dropped(room, applied, option)
        if reason:
            resolution.dropped.append({"option": number, "reason": reason})
            continue
        applied = _combined(applied, option.edits)
        resolution.applied.append(number)
    resolution.completion = edits_json(applied)
    return resolution


def _legal_construction(room: SceneGraph, edits: TrainingEdits) -> TrainingEdits:
    """The answer's construction, or none of it when it is unbuildable or relocates a fixture onto something."""
    construction = TrainingEdits(wall_shifts=edits.wall_shifts, fixture_moves=edits.fixture_moves)
    return construction if _legal(room, construction) is not None else EMPTY


def resolve_free_moves(room: SceneGraph, edits: TrainingEdits, pinned=frozenset()) -> Resolution:
    """A free-form answer with each furniture move snapped to the nearest legal spot (`fix/snap.py`)."""
    construction = _legal_construction(room, edits)
    built = build(room, construction.wall_shifts, construction.fixture_moves)
    asked = [move for move in node_moves(edits) if move.node_id not in pinned]
    snapped = snap_moves(built, asked)
    kept = TrainingEdits(moves=[_furniture(move) for move in snapped.kept], wall_shifts=construction.wall_shifts,
                         fixture_moves=construction.fixture_moves)
    return Resolution(completion=edits_json(kept), interface="free_moves", snapped={
        "asked": len(edits.moves), "kept": len(snapped.kept), "dropped": len(edits.moves) - len(snapped.kept),
        "nudged_inches": {str(node_id)[:4]: round(to_inches(meters), 1)
                          for node_id, meters in snapped.nudged_meters.items()},
        "construction_dropped": construction is EMPTY and bool(edits.wall_shifts or edits.fixture_moves),
    })


def resolve(reply: str, room: SceneGraph, menu: Menu, pinned=frozenset()) -> Resolution:
    """Whatever the model answered, as edits that are legal by construction."""
    choice = parse_choice(reply)
    if choice is not None:
        return resolve_choice(room, menu, choice)
    edits = parse_edits(reply)
    if edits is not None:
        return resolve_free_moves(room, edits, pinned)
    return Resolution(completion=edits_json(EMPTY), interface="unparseable")
