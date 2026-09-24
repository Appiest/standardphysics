"""Which rooms the model is asked about, and which of its answers survive the whole scan.

A small scan (at most `SMALL_SCAN_NODES` nodes) is one room, and one prompt,
as it was in training. A bigger scan is cut the way training cut it
(`training.rooms.build_window`): a window of `WINDOW_RADIUS_METERS` around a
furniture-fixable problem, with its own local route. At most
`MAX_WINDOWS_PER_SUGGESTION` windows are asked about per click, the problems
with the largest shortfall first, and each window gets the full four answers.

Every window's best accepted answer is carried back into the whole scan and
the combination is scored there, with the whole scan's hard constraints and
gate. When the combination fails, the single windows are tried alone, best
reward first, and the first one the whole scan accepts is the suggestion.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Callable

from standardphysics_agents.evaluation.gate import UNMEASURED_SHORTFALL_INCHES
from standardphysics_agents.redesign import FurnitureMove, RoomEdits
from standardphysics_agents.training import TrainingChecker, edits_json, parse_edits, prompt_messages
from standardphysics_agents.training.edits import node_moves
from standardphysics_agents.training.phantoms import phantoms, pin, scan_errors, without_nodes, without_unmeasured
from standardphysics_agents.training.reward import Verdict, score_completion
from standardphysics_agents.training.rooms import ScanPlan, build_window, movable_named
from standardphysics_agents.training.windows import WINDOW_RADIUS_METERS, summarize_problem
from standardphysics_contracts import Finding, NodeMove, Scenario, SceneGraph

MAX_WINDOWS_PER_SUGGESTION = 5
"""Windows asked about per click on a big scan; each costs one model request of four answers."""

SEPARATE_WINDOWS_METERS = WINDOW_RADIUS_METERS / 2
"""Problems closer than this share a window, as they did in training."""

Ask = Callable[[list[dict]], list[str]]


@dataclass
class Part:
    """One prompt: the whole small scan, or one window of a big one."""

    graph: SceneGraph
    checker: TrainingChecker


@dataclass
class Answer:
    verdict: Verdict
    moves: list[NodeMove]


@dataclass
class Search:
    """What the model was asked, what it answered, and the layout that survived, if any."""

    parts: int = 0
    model_calls: int = 0
    verdicts: list[Verdict] = field(default_factory=list)
    chosen: Answer | None = None
    whole_scan_reason: str | None = None
    """Why the combined windows failed on the whole scan, when they did."""


def scan_plan(scan_id: uuid.UUID, graph: SceneGraph, scenario: Scenario) -> ScanPlan:
    """The scan as training saw it: unmeasured boxes and floating scan errors out, phantoms pinned."""
    measured = without_unmeasured(graph)
    pinned = phantoms(measured)
    removed = scan_errors(measured, pinned)
    cleaned = without_nodes(measured, {node.id for node in removed})
    return ScanPlan(str(scan_id), pin(cleaned, pinned), scenario, pinned, [], removed)


def whole_checker(plan: ScanPlan) -> TrainingChecker:
    return TrainingChecker(plan.scenario, pinned=frozenset(item.node_id for item in plan.pinned),
                           owner_layout=plan.graph)


def _shortfall(finding: Finding) -> float:
    if finding.measured_inches is None or finding.required_inches is None:
        return UNMEASURED_SHORTFALL_INCHES
    return abs(finding.required_inches - finding.measured_inches)


def _far_from_every(summary: dict, chosen: list[dict]) -> bool:
    x, y = summary["at"]
    return all((x - s["at"][0]) ** 2 + (y - s["at"][1]) ** 2 > SEPARATE_WINDOWS_METERS ** 2 for s in chosen)


def window_seeds(plan: ScanPlan, problems: list[Finding]) -> list[dict]:
    """Problems to cut windows around: ones naming a movable piece, largest shortfall first, spaced apart."""
    ranked = sorted((p for p in problems if movable_named(plan.graph, p)), key=_shortfall, reverse=True)
    chosen: list[dict] = []
    for problem in ranked:
        summary = summarize_problem(problem)
        if summary["at"] is not None and _far_from_every(summary, chosen):
            chosen.append(summary)
    return chosen[:MAX_WINDOWS_PER_SUGGESTION]


def _window_part(plan: ScanPlan, seed: dict, index: int) -> Part | None:
    window, _ = build_window(plan, tuple(seed["at"]), seed, f"{plan.scan_id[:8]}:s{index}")
    if window is None:
        return None
    pinned = frozenset(uuid.UUID(node_id) for node_id in window.pinned)
    return Part(window.graph, TrainingChecker(window.scenario, pinned=pinned, owner_layout=window.graph))


def parts_to_ask(plan: ScanPlan, checker: TrainingChecker, problems: list[Finding]) -> list[Part]:
    if plan.small:
        return [Part(plan.graph, checker)]
    parts = (_window_part(plan, seed, index) for index, seed in enumerate(window_seeds(plan, problems)))
    return [part for part in parts if part is not None]


def _best_answer(part: Part, completions: list[str], search: Search) -> Answer | None:
    best = None
    for text in completions:
        verdict = score_completion(text, part.graph, part.checker)
        search.verdicts.append(verdict)
        if verdict.gate_accepts and (best is None or verdict.reward > best.verdict.reward):
            best = Answer(verdict, node_moves(parse_edits(text)))
    return best


def _as_completion(moves: list[NodeMove]) -> str:
    return edits_json(RoomEdits(moves=[
        FurnitureMove(node_id=move.node_id, dx=move.delta_translation.x, dy=move.delta_translation.y,
                      rotation_degrees=move.delta_rotation_z_degrees)
        for move in moves
    ]))


def combined_moves(answers: list[Answer]) -> list[NodeMove]:
    """Every window's moves together; a piece two windows both moved keeps the move from the higher reward."""
    taken: dict = {}
    for answer in sorted(answers, key=lambda a: a.verdict.reward, reverse=True):
        for move in answer.moves:
            taken.setdefault(move.node_id, move)
    return list(taken.values())


def on_whole_scan(plan: ScanPlan, checker: TrainingChecker, moves: list[NodeMove]) -> Answer:
    return Answer(score_completion(_as_completion(moves), plan.graph, checker), moves)


def _whole_scan_choice(plan: ScanPlan, checker: TrainingChecker, answers: list[Answer], search: Search) -> None:
    combined = on_whole_scan(plan, checker, combined_moves(answers))
    if combined.verdict.gate_accepts:
        search.chosen = combined
        return
    search.whole_scan_reason = combined.verdict.reason
    if len(answers) < 2:
        return
    for answer in sorted(answers, key=lambda a: a.verdict.reward, reverse=True):
        alone = on_whole_scan(plan, checker, answer.moves)
        if alone.verdict.gate_accepts:
            search.chosen = alone
            return


def search(plan: ScanPlan, checker: TrainingChecker, problems: list[Finding], ask: Ask) -> Search:
    """Ask about every part, keep each part's best accepted answer, and settle on one layout for the scan."""
    result = Search()
    parts = parts_to_ask(plan, checker, problems)
    result.parts = len(parts)
    answers = []
    for part in parts:
        completions = ask(prompt_messages(part.graph, part.checker))
        result.model_calls += 1
        best = _best_answer(part, completions, result)
        if best is not None:
            answers.append(best)
    if not answers:
        return result
    if plan.small:
        result.chosen = answers[0]
        return result
    _whole_scan_choice(plan, checker, answers, result)
    return result
