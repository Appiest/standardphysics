"""A menu of legal moves: the model picks from options code has already checked and measured.

Asked for raw `{"moves":[{"node_id","dx","dy","rotation_degrees"}]}`, the base
model had 57-77% of its answers refused for collisions or leaving the floor on
square rooms, and on rotated rooms it mostly answered with no moves at all.
Models that write coordinates fail this way; systems that never place anything
illegally let code own the geometry. So the model here chooses, and code places.

1. Generate. For each fixable problem, the solver's own guesses: the slide
   ladder of `fix/strategies.py` and the placement beam of `fix/placement.py`,
   and slides that set a too-high item down on a lower surface
   (`fix/surfaces.py`). When none of those clears the problem, three more
   families get their own tries: every piece inside a turning circle pushed
   out at once (`fix/clearing.py`), a table carried together with its seats
   (`fix/groups.py`), and short nudges of each named piece along its own sides
   (`fix/nudges.py`). Last come short slides of a built-in fixture the problem
   names, alone or together with the built-ins it touches (`fix/built_ins.py`),
   so a counter keeps its lowered section. Each is worded as a relation
   ("slide Chair [3f2a] 14 in away from the Cafe table, for P1").
   After Holodeck (Yang et al., CVPR 2024, arXiv:2312.09067), where the language
   model states relations and a solver enforces no-collision and in-bounds.
2. Mask. Only guesses `reward.constrained` finds nothing wrong with survive:
   `fix.violations`, `relocation_violations` for a relocated fixture, and the
   veto of any ADA layout directive for the room's space type, the same test
   that refuses an answer. An illegal move can never be offered.
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
import random
import re
from dataclasses import dataclass, field
from itertools import chain, zip_longest
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from standardphysics_contracts import Finding, NodeMove, SceneGraph, SceneNode, to_inches, to_meters
from standardphysics_pipeline.footprints import rotation_about_z

from ..assess import Pass
from ..checks import roles
from ..evaluation.gate import accepts
from ..fix import CandidateRejection, candidates, combine_rejections, pinch_from, room_heading, snap_moves
from ..fix.budget import out_of_time
from ..fix.built_ins import built_in_set_moves
from ..fix.clearing import circle_clearing_moves
from ..fix.groups import group_moves
from ..fix.nudges import nudge_moves
from ..fix.placement import placements
from ..fix.strategies import Candidate
from ..fix.surfaces import lower_surface_moves
from ..redesign import FurnitureMove
from .catalog import ACCESSIBLE_FOUR_TOP, ACCESSIBLE_TWO_TOP, LOWERED_COUNTER_SECTION
from .checker import TrainingChecker
from .construction import MAX_FIXTURE_MOVE_INCHES, FixtureMove, build, construction_inches, fixture_ids
from .edits import TrainingEdits, _json_text, combined, edits_json, node_moves, parse_edits
from .fittings import HeightChange, LoweredSection, Replacement, height_range, rests_on, use_of
from .owner import WishBook
from .prices import construction_price
from .prompt import room_view
from .quality import seat_table_pairs
from .reward import constrained, touched
from .usability import usability
from .wishes import Wish, infer_wishes, kept

GUESSES_PER_PROBLEM = 24
PLACEMENTS_PER_PROBLEM = 24
FIXTURE_STEPS_INCHES = (6.0, 12.0, MAX_FIXTURE_MOVE_INCHES)
FIXTURE_DIRECTIONS = tuple((math.cos(math.radians(angle)), math.sin(math.radians(angle)))
                           for angle in range(0, 360, 45))
FURNITURE_TRIES = 12
CLEARING_TRIES = 18
FIXTURE_TRIES = 12
FITTING_TRIES = 8
"""Legal guesses measured per problem; each costs one full checker pass.

The clearing families and then fixture slides are only measured for a problem
no earlier option clears, and fixture slides are construction, so they come
last.
"""
OPTIONS_PER_PROBLEM = 4
MENU_SIZE = 12
MAX_PICKS = MENU_SIZE
TURN_WORDING_DEGREES = 1.0
SQUARE_TOLERANCE_DEGREES = 3.0
"""How close to a multiple of 90 degrees, relative to the room's own axes, still counts as square."""

MENU_INSTRUCTION = (
    "You are rearranging a shop so a wheelchair user can get around it. `problems` lists the measured "
    "accessibility problems furniture can address, each with a label such as P1. `options` lists moves that "
    "have already been checked: every one is legal (no collisions, stays on the floor, keeps doors clear) and "
    "was measured on its own, so its `clears`, `improves`, `usable` and `inches_moved` are facts, not guesses. "
    "Pick the options that together clear the most problems with the least disruption. Options apply in the "
    "order you list them; one that would clash with an earlier pick or moves the same piece again is skipped "
    "and reported to you, and `clashes_with` names the options each one cannot be combined with. "
    "`last_result` says what happened to your previous answer, if any, including what the owner said. "
    "`owner_wishes` lists what the owner wants kept: stated ones are the owner's own words and no option breaks "
    "them; inferred ones are read from where things stand now, and `breaks_wishes` on an option names any it "
    "would break. Prefer options that keep the owner's layout. An option marked (construction) needs a "
    "contractor; `construction_cost` compares what each costs, so prefer a move, then the cheapest construction."
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


_ID_TAG = re.compile(r" \[[0-9a-f]{4}\]")
_PROBLEM_LABEL = re.compile(r"\bP\d+\b")
_FOR_PROBLEM = re.compile(r",? for P\d+$")
_CONSTRUCTION_TAG = re.compile(r" \(construction\)$")


def _quoted_title(titles: dict[str, str], label: str) -> str:
    return f'"{titles[label]}"' if label in titles else label


@dataclass(frozen=True)
class Menu:
    problems: dict[UUID, str]
    """Each current fixable problem's finding id, and the label the model reads it by."""
    options: list[Option]
    problem_view: list[dict] = field(default_factory=list)
    veto: CandidateRejection | None = None
    """The room's directive refusal and the owner's stated wishes, which picks and free-form construction are
    held to as well."""
    wish_view: list[dict] = field(default_factory=list)
    """The owner's wishes as the model reads them."""

    def option(self, number: int) -> Option | None:
        return next((option for option in self.options if option.number == number), None)

    def in_owner_words(self, text: str) -> str:
        """Model-facing text without piece id tags, each problem label replaced by its finding's title."""
        titles = {view["label"]: view["title"] for view in self.problem_view}
        untagged = _ID_TAG.sub("", text)
        return _PROBLEM_LABEL.sub(lambda match: _quoted_title(titles, match.group(0)), untagged)

    def picked_in_owner_words(self, number: int) -> str:
        """An option's wording for the owner, without the problem it was offered for or its construction tag."""
        option = self.option(number)
        assert option is not None
        return self.in_owner_words(_CONSTRUCTION_TAG.sub("", _FOR_PROBLEM.sub("", option.wording)))


@dataclass(frozen=True)
class MenuView:
    """How the menu is shown: ranked best first or shuffled, and whether inferred wishes are labelled.

    Shuffling and hiding the inferred wishes leave the model to judge the
    options and read the owner's layout itself, which is what an evaluation of
    the model's own judgment needs.
    """

    order: str = "ranked"
    wishes_shown: bool = True
    seed: int = 0


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
    return "the problem spot", (point.x, point.y)


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


def _pinch_candidates(graph: SceneGraph, finding: Finding, checker: TrainingChecker) -> list[Candidate]:
    pinch = pinch_from(finding, graph)
    if pinch is None or not pinch.fixable:
        return []
    return [*candidates(pinch, GUESSES_PER_PROBLEM),
            *placements(graph, pinch, finding, checker.rules, PLACEMENTS_PER_PROBLEM)]


def _surface_guesses(graph: SceneGraph, finding: Finding, label: str) -> list[tuple[float, _Guess]]:
    return [(found.candidate.disruption, _Guess(
        TrainingEdits(moves=[_furniture(move) for move in found.candidate.moves]),
        f"set {_name(found.item)} down on the {found.surface.label}, "
        f"{to_inches(found.candidate.disruption):.0f} in away, for {label}"))
        for found in lower_surface_moves(graph, finding)]


def _furniture_guesses(graph: SceneGraph, finding: Finding, checker: TrainingChecker, label: str) -> list[_Guess]:
    """Slides and placements that open space, and slides that set a too-high item on a lower surface."""
    found = [(candidate.disruption, _Guess(
        TrainingEdits(moves=[_furniture(move) for move in candidate.moves]),
        "; ".join(_move_words(graph, move, finding) for move in candidate.moves) + f", for {label}"))
        for candidate in _pinch_candidates(graph, finding, checker)]
    found.extend(_surface_guesses(graph, finding, label))
    found.sort(key=lambda pair: pair[0])
    return _varied([guess for _, guess in found if not touched(guess.edits) & set(checker.pinned)])


def _groups(graph: SceneGraph) -> list[frozenset]:
    """Each table with the seats standing at it, as the room's layout shows them."""
    by_table: dict = {}
    for seat, table in seat_table_pairs(graph):
        by_table.setdefault(table, {table}).add(seat)
    return [frozenset(members) for members in by_table.values()]


def _set_words(graph: SceneGraph, candidate: Candidate, finding: Finding) -> str:
    table = max((graph.by_id(move.node_id) for move in candidate.moves),
                key=lambda node: node.dimensions.x * node.dimensions.y)
    delta = candidate.moves[0].delta_translation
    seats = len(candidate.moves) - 1
    return f"{_slide_words(graph, table, finding, delta.x, delta.y, 'slide')} with its {seats} seat{'s' * (seats > 1)}"


def _clearing_guesses(graph: SceneGraph, finding: Finding, checker: TrainingChecker, label: str) -> list[_Guess]:
    """A turning circle emptied at once, a table moved with its seats, and short nudges, taken in turn."""
    pinned = frozenset(checker.pinned)

    def worded(candidate: Candidate, words: str) -> _Guess:
        return _Guess(TrainingEdits(moves=[_furniture(move) for move in candidate.moves]), f"{words}, for {label}")

    def slides(candidate: Candidate) -> str:
        return "; ".join(_move_words(graph, move, finding) for move in candidate.moves)

    families = [
        [worded(found, slides(found)) for found in circle_clearing_moves(graph, finding, pinned)],
        _varied([worded(found, _set_words(graph, found, finding))
                 for found in group_moves(graph, finding, _groups(graph), pinned)]),
        _varied([worded(found, slides(found)) for found in nudge_moves(graph, finding, pinned)]),
    ]
    return [guess for guess in chain.from_iterable(zip_longest(*families)) if guess is not None]


def _varied(guesses: list[_Guess]) -> list[_Guess]:
    """The least disruptive guess for each set of pieces first, then the next of each, and so on.

    A region blocked by two chairs clears only when both move, and sorted by
    disruption alone every single-chair slide would use up the tries first.
    """
    by_pieces: dict[frozenset, list[_Guess]] = {}
    for guess in guesses:
        by_pieces.setdefault(frozenset(touched(guess.edits)), []).append(guess)
    return [guess for guess in chain.from_iterable(zip_longest(*by_pieces.values())) if guess is not None]


def _furniture(move: NodeMove) -> FurnitureMove:
    return FurnitureMove(node_id=move.node_id, dx=move.delta_translation.x, dy=move.delta_translation.y,
                         rotation_degrees=move.delta_rotation_z_degrees)


def _single_fixture_guesses(graph: SceneGraph, finding: Finding, fixtures: set, label: str) -> list[_Guess]:
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


def _fixture_move(move: NodeMove) -> FixtureMove:
    return FixtureMove(node_id=move.node_id, dx_inches=round(to_inches(move.delta_translation.x), 1),
                       dy_inches=round(to_inches(move.delta_translation.y), 1))


def _run_words(graph: SceneGraph, candidate: Candidate, finding: Finding) -> str:
    first, *rest = (graph.by_id(move.node_id) for move in candidate.moves)
    delta = candidate.moves[0].delta_translation
    words = _slide_words(graph, first, finding, delta.x, delta.y, "move built-in")
    return f"{words} with the {', '.join(node.label for node in rest)} it touches" if rest else words


def _fixture_set_guesses(graph: SceneGraph, finding: Finding, fixtures: set, label: str) -> list[_Guess]:
    return [_Guess(TrainingEdits(fixture_moves=[_fixture_move(move) for move in found.moves]),
                   f"{_run_words(graph, found, finding)} (construction), for {label}")
            for found in built_in_set_moves(graph, finding, fixtures)]


def _fixture_guesses(graph: SceneGraph, finding: Finding, checker: TrainingChecker, label: str) -> list[_Guess]:
    """Slides of a built-in the problem names, alone or with the built-ins it touches; construction, so offered last."""
    fixtures = fixture_ids(graph) - set(checker.pinned)
    families = [_single_fixture_guesses(graph, finding, fixtures, label),
                _fixture_set_guesses(graph, finding, fixtures, label)]
    return [guess for guess in chain.from_iterable(zip_longest(*families)) if guess is not None]


def _named_pieces(graph: SceneGraph, finding: Finding) -> list[SceneNode]:
    ids = {node.id for node in graph.nodes}
    return [graph.by_id(node_id) for node_id in (finding.locus.node_ids if finding.locus else []) if node_id in ids]


def _section_guesses(graph: SceneGraph, node: SceneNode, label: str) -> list[_Guess]:
    """A lowered section cut into either end of a counter, with whatever people pay at set down on it."""
    if use_of(graph, node) != "counter":
        return []
    paying = [item.id for item in roles.point_of_sale(graph) if rests_on(item, node)][:4]
    carried = f", with the {', '.join(graph.by_id(item).label for item in paying)} set on it" if paying else ""
    return [_Guess(TrainingEdits(add_lowered_section=[LoweredSection(counter_id=node.id, end=end, carry=paying)]),
                   f"cut a {LOWERED_COUNTER_SECTION.length_inches:g} in section at {placing} of {_name(node)} "
                   f"down to {LOWERED_COUNTER_SECTION.top_inches:g} in{carried} (construction), for {label}")
            for end, placing in (("start", "one end"), ("end", "the other end"))]


def _target_tops(finding: Finding, allowed: tuple[float, float]) -> list[float]:
    """Tops that meet the rule's number with a little to spare, inside what the piece can be built or hung at."""
    required = finding.required_inches
    if required is None:
        return []
    measured = finding.measured_inches
    lowering = measured is None or measured > required
    tops = (required - 2.0, required) if lowering else (required + 2.0, required)
    return sorted({round(min(max(top, allowed[0]), allowed[1]), 1) for top in tops})


def _height_guesses(graph: SceneGraph, node: SceneNode, finding: Finding, label: str) -> list[_Guess]:
    allowed = height_range(graph, node)
    if allowed is None:
        return []
    verb = "rehang" if use_of(graph, node) is None else "rebuild"
    return [_Guess(TrainingEdits(height_changes=[HeightChange(node_id=node.id, top_inches=top)]),
                   f"{verb} {_name(node)} with its top at {top:g} in (construction), for {label}")
            for top in _target_tops(finding, allowed)]


def _replacement_guesses(graph: SceneGraph, node: SceneNode, label: str) -> list[_Guess]:
    if use_of(graph, node) != "surface":
        return []
    return [_Guess(TrainingEdits(replacements=[Replacement(node_id=node.id, catalog_item=item.name)]),
                   f"swap {_name(node)} for a {item.length_inches:g} in {item.label.lower()} "
                   f"{item.top_inches:g} in high (construction), for {label}")
            for item in (ACCESSIBLE_TWO_TOP, ACCESSIBLE_FOUR_TOP)]


def _fitting_guesses(graph: SceneGraph, finding: Finding, checker: TrainingChecker, label: str) -> list[_Guess]:
    """Construction that changes what a piece is rather than where it stands, for problems no move can clear:
    a lowered counter section, a piece rebuilt or rehung at a reachable height, a table swapped for one at
    dining height. Offered only when the checker counts fitting edits as fixes."""
    if not checker.fittable(finding.check_id):
        return []
    families = [guesses for node in _named_pieces(graph, finding) if node.id not in checker.pinned
                for guesses in (_section_guesses(graph, node, label), _height_guesses(graph, node, finding, label),
                                _replacement_guesses(graph, node, label))]
    return [guess for guess in chain.from_iterable(zip_longest(*families)) if guess is not None]


TIERS = ((_furniture_guesses, FURNITURE_TRIES), (_clearing_guesses, CLEARING_TRIES),
         (_fixture_guesses, FIXTURE_TRIES), (_fitting_guesses, FITTING_TRIES))
"""Guess families in the order they are measured, each with its own tries."""


def _legal(room: SceneGraph, edits: TrainingEdits, veto: CandidateRejection | None = None) -> SceneGraph | None:
    """The room these edits make, or None when any hard constraint breaks or a directive refuses it."""
    try:
        legality = constrained(room, edits, veto)
    except ValueError:
        return None
    return None if legality.refusal else legality.candidate


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
        "construction_cost": construction_price(room, edits),
        "ends_square": _ends_square(room, candidate, edits),
    }


def _inches(finding: Finding) -> float | None:
    return None if finding.measured_inches is None else round(finding.measured_inches, 1)


def _node_heading_degrees(node: SceneNode) -> float:
    cos_t, sin_t = rotation_about_z(node)
    return math.degrees(math.atan2(sin_t, cos_t))


def _square_to_room(heading_degrees: float, room_heading_degrees: float,
                    tolerance: float = SQUARE_TOLERANCE_DEGREES) -> bool:
    """Whether a heading sits within `tolerance` degrees of a multiple of 90, relative to the room's own axes."""
    relative = (heading_degrees - room_heading_degrees) % 90.0
    return min(relative, 90.0 - relative) <= tolerance


def _ends_square(room: SceneGraph, candidate: SceneGraph, edits: TrainingEdits) -> bool:
    """Whether every piece these edits move ends up square to the room, not standing at an angle in it."""
    changed = touched(edits)
    heading = room_heading(room)
    if heading is None or not changed:
        return True
    room_degrees = math.degrees(heading)
    by_id = {node.id: node for node in candidate.nodes}
    return all(_square_to_room(_node_heading_degrees(by_id[node_id]), room_degrees)
               for node_id in changed if node_id in by_id)


def _rank(effect: dict) -> tuple:
    return (-len(effect["clears"]), effect["fixable_left"], int(not effect.get("ends_square", True)),
            len(effect.get("breaks_wishes", [])), effect.get("construction_cost", 0.0), effect["construction_inches"],
            -effect["usable"], effect["inches_moved"])


def _drop_covered_diagonals(measured: list) -> list:
    """Drop a diagonal option when a square option clears the very same problems.

    A diagonal option is kept when it is the only one that clears a problem at
    all: a display case standing square that fixes nothing is not an
    alternative, so nothing else works and the diagonal move stays offered.
    """
    square_clears = {frozenset(effect["clears"]) for _, effect in measured
                     if effect.get("ends_square", True) and effect["clears"]}
    return [pair for pair in measured
            if pair[1].get("ends_square", True) or frozenset(pair[1]["clears"]) not in square_clears]


def _clears(found: list, label: str) -> bool:
    return any(label in effect["clears"] for _, effect in found)


def _ordered(measured: list, view: MenuView) -> list:
    if view.order == "shuffled":
        shuffled = list(measured)
        random.Random(view.seed).shuffle(shuffled)
        return shuffled
    return sorted(measured, key=lambda pair: _rank(pair[1]))


def _wishes_shown(room: SceneGraph, checker: TrainingChecker, stated: WishBook | None,
                  view: MenuView) -> tuple[list[tuple[str, Wish]], list[dict]]:
    """Inferred wishes to label options with, and every wish the model is told about."""
    inferred = infer_wishes(room, checker.measure) if view.wishes_shown else []
    labelled = [(f"W{index}", wish) for index, wish in enumerate(inferred, start=1)]
    said = stated.stated if stated else []
    told = [*[{"label": label, "wish": wish.text, "source": "inferred"} for label, wish in labelled],
            *[{"wish": wish.text, "source": "stated"} for wish in said]]
    return labelled, told


def _problem_view(graph: SceneGraph, problems: list[Finding], labels: dict[UUID, str]) -> list[dict]:
    names = {node.id: _name(node) for node in graph.nodes}
    return [{"label": labels[finding.id], "check": finding.check_id, "title": finding.title,
             "measured_inches": _inches(finding), "required_inches": finding.required_inches,
             "involves": [names.get(node_id, "?") for node_id in (finding.locus.node_ids if finding.locus else [])]}
            for finding in problems]


@dataclass(frozen=True)
class MenuLimits:
    """How much of the room a menu is built for.

    `focus` names the findings to generate options for, or None for every
    fixable problem; options are still labelled with everything they clear.
    `deadline` is a `time.monotonic()` time after which no further guess is
    generated or measured, and the menu is built from what was measured by then.
    """

    focus: frozenset[UUID] | None = None
    deadline: float | None = None

    def wants(self, finding: Finding) -> bool:
        return self.focus is None or finding.id in self.focus


@dataclass
class _Measurer:
    """Legal guesses measured against one room, each worded differently from every option already kept."""

    room: SceneGraph
    checker: TrainingChecker
    before: Pass
    labels: dict[UUID, str]
    veto: CandidateRejection | None = None
    wishes: list[tuple[str, Wish]] = field(default_factory=list)
    worded: set = field(default_factory=set)
    deadline: float | None = None

    def breaks(self, candidate: SceneGraph) -> list[str]:
        return [label for label, wish in self.wishes if not kept(wish, self.room, candidate, self.checker.measure)]

    def measured(self, guess: _Guess, candidate: SceneGraph) -> dict | None:
        """What the guess does to the room, or None when the gate refuses it."""
        after = self.checker.assess(candidate)
        if not accepts(self.before, after):
            return None
        self.worded.add(guess.wording)
        effect = _effect(self.room, candidate, self.checker, self.before, after, self.labels, guess.edits)
        return {**effect, "breaks_wishes": self.breaks(candidate)} if self.wishes else effect

    def options(self, guesses: list[_Guess], tries: int, label: str) -> list[tuple[_Guess, dict]]:
        """Up to `tries` legal guesses measured, the best few of them kept.

        Measuring stops early once enough options are found and one of them
        clears the problem. Until then it goes on, so a family whose first few
        guesses only improve the problem still gets to try its larger moves.
        """
        found: list[tuple[_Guess, dict]] = []
        for guess in guesses:
            if tries == 0 or out_of_time(self.deadline) or (len(found) >= OPTIONS_PER_PROBLEM
                                                            and _clears(found, label)):
                break
            candidate = None if guess.wording in self.worded else _legal(self.room, guess.edits, self.veto)
            if candidate is None:
                continue
            tries -= 1
            effect = self.measured(guess, candidate)
            if effect is not None:
                found.append((guess, effect))
        return sorted(found, key=lambda pair: _rank(pair[1]))[:OPTIONS_PER_PROBLEM]

    def for_problem(self, finding: Finding) -> list[tuple[_Guess, dict]]:
        """Each tier of guesses in turn, stopping at the first tier that offers an option clearing the problem."""
        label = self.labels[finding.id]
        found: list[tuple[_Guess, dict]] = []
        for guesses, tries in TIERS:
            if out_of_time(self.deadline):
                break
            found.extend(self.options(guesses(self.room, finding, self.checker, label), tries, label))
            if _clears(found, label):
                break
        return found


def build_menu(room: SceneGraph, checker: TrainingChecker, stated: WishBook | None = None,
               view: MenuView = MenuView(), limits: MenuLimits = MenuLimits()) -> Menu:
    """Legal, gate-accepted options for each of the room's fixable problems, numbered from 1.

    Nothing offered breaks a hard constraint, a directive for the room's space
    type, or a wish the owner stated. `limits` can narrow the problems options
    are generated for and bound the time spent measuring them.
    """
    before = checker.assess(room)
    problems = checker.fixable_problems(before)
    labels = {finding.id: f"P{index}" for index, finding in enumerate(problems, start=1)}
    veto = combine_rejections(checker.directive_veto(room), stated.rejection(checker.measure) if stated else None)
    labelled, told = _wishes_shown(room, checker, stated, view)
    measurer = _Measurer(room, checker, before, labels, veto, labelled, deadline=limits.deadline)
    measured = [pair for finding in problems if limits.wants(finding) for pair in measurer.for_problem(finding)]
    measured = _drop_covered_diagonals(measured)
    kept_best = sorted(measured, key=lambda pair: _rank(pair[1]))[:MENU_SIZE]
    options = [Option(number, guess.wording, guess.edits, effect)
               for number, (guess, effect) in enumerate(_ordered(kept_best, view), start=1)]
    for option in options:
        option.effect["clashes_with"] = [other.number for other in options if other is not option
                                         and _why_dropped(room, option.edits, other, veto)]
    return Menu(problems=labels, options=options, problem_view=_problem_view(room, problems, labels), veto=veto,
                wish_view=told)


def menu_messages(room: SceneGraph, checker: TrainingChecker, menu: Menu, last_result: dict | None) -> list[dict]:
    """System, then one user message: the current room, its problems, the menu and the last result."""
    view = room_view(room, checker.scenario, [])
    view.pop("problems", None)
    view.pop("walls_you_can_move", None)
    content = {"problems": menu.problem_view, "options": [option.as_prompt() for option in menu.options],
               "last_result": last_result, "room": view}
    if menu.wish_view:
        content["owner_wishes"] = menu.wish_view
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


def _why_dropped(room: SceneGraph, applied: TrainingEdits, option: Option | None,
                 veto: CandidateRejection | None) -> str | None:
    if option is None:
        return "no such option"
    if touched(applied) & touched(option.edits):
        return "moves a piece an earlier pick already moved"
    try:
        return constrained(room, combined(applied, option.edits), veto).refusal
    except ValueError:
        return "unbuildable construction"


def resolve_choice(room: SceneGraph, menu: Menu, choice: MenuChoice) -> Resolution:
    """Picks applied in order; any that became illegal after earlier ones is dropped and reported."""
    resolution = Resolution(completion="", interface="choose", picks=list(choice.choose), why=choice.why)
    applied = EMPTY
    for number in choice.choose:
        option = menu.option(number)
        reason = _why_dropped(room, applied, option, menu.veto)
        if reason:
            resolution.dropped.append({"option": number, "reason": reason})
            continue
        assert option is not None
        applied = combined(applied, option.edits)
        resolution.applied.append(number)
    resolution.completion = edits_json(applied)
    return resolution


def _legal_construction(room: SceneGraph, edits: TrainingEdits, veto: CandidateRejection | None) -> TrainingEdits:
    """The answer's construction, or none of it when it is unbuildable or `reward.constrained` refuses it."""
    construction = TrainingEdits(wall_shifts=edits.wall_shifts, fixture_moves=edits.fixture_moves)
    return construction if _legal(room, construction, veto) is not None else EMPTY


def resolve_free_moves(room: SceneGraph, edits: TrainingEdits, pinned=frozenset(),
                       veto: CandidateRejection | None = None) -> Resolution:
    """A free-form answer with each furniture move snapped to the nearest legal spot (`fix/snap.py`).

    Snapping keeps furniture off other pieces and on the floor; a snapped move a
    directive refuses is left for the scorer to refuse.
    """
    construction = _legal_construction(room, edits, veto)
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
        return resolve_free_moves(room, edits, pinned, menu.veto)
    return Resolution(completion=edits_json(EMPTY), interface="unparseable")
