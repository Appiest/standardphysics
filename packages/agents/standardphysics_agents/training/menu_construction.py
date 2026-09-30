"""Construction the menu offers when no furniture move clears a problem: a built-in slid alone or with the
built-ins it touches, and fittings that change what a piece is, such as a lowered counter section."""

from __future__ import annotations

import math
from itertools import chain, zip_longest

from standardphysics_contracts import Finding, NodeMove, SceneGraph, SceneNode, to_inches, to_meters

from ..checks import roles
from ..fix.built_ins import built_in_set_moves
from ..fix.strategies import Candidate
from .catalog import ACCESSIBLE_FOUR_TOP, ACCESSIBLE_TWO_TOP, LOWERED_COUNTER_SECTION
from .checker import TrainingChecker
from .construction import MAX_FIXTURE_MOVE_INCHES, FixtureMove, fixture_ids
from .edits import MAX_FIXTURE_MOVES, TrainingEdits
from .fittings import HeightChange, LoweredSection, Replacement, height_range, rests_on, use_of
from .menu_words import _Guess, _name, _slide_words

FIXTURE_STEPS_INCHES = (6.0, 12.0, MAX_FIXTURE_MOVE_INCHES)


FIXTURE_DIRECTIONS = tuple((math.cos(math.radians(angle)), math.sin(math.radians(angle)))
                           for angle in range(0, 360, 45))


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
    """A built-in slid with the built-ins it touches, unless that is more than one answer may move."""
    return [_Guess(TrainingEdits(fixture_moves=[_fixture_move(move) for move in found.moves]),
                   f"{_run_words(graph, found, finding)} (construction), for {label}")
            for found in built_in_set_moves(graph, finding, fixtures) if len(found.moves) <= MAX_FIXTURE_MOVES]


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
