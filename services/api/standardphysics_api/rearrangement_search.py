"""Which rooms the model is asked about, and which of its answers survive the whole scan.

A small scan (at most `SMALL_SCAN_NODES` nodes) is one room, and one prompt,
as it was in training. A bigger scan is cut the way training cut it
(`training.rooms.build_window`): a window of `WINDOW_RADIUS_METERS` around a
furniture-fixable problem, with its own local route. At most
`MAX_WINDOWS_PER_SUGGESTION` windows are asked about per click, the problems
with the largest shortfall first, and each window gets up to four rounds.

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
from standardphysics_agents.training import TrainingChecker, edits_json
from standardphysics_agents.training.edits import moves_between
from standardphysics_agents.training.feedback import Attempt, diagnose, feedback_message
from standardphysics_agents.training.phantoms import phantoms, pin, scan_errors, without_nodes, without_unmeasured
from standardphysics_agents.training.rooms import ScanPlan, build_window, movable_named
from standardphysics_agents.training.snapped_prompt import openrouter_prompt_messages, prompt_messages
from standardphysics_agents.training.snapped_reward import Verdict, judge
from standardphysics_agents.training.windows import WINDOW_RADIUS_METERS, summarize_problem
from standardphysics_contracts import Finding, NodeMove, Scenario, SceneGraph, graph_hash

from .openrouter_rearrange import BudgetReached

MAX_WINDOWS_PER_SUGGESTION = 5
MAX_ROUNDS = 4
"""Each window gets one conversation of at most four proposals."""

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
    snapped: bool = False


@dataclass
class Search:
    """What the model was asked, what it answered, and the layout that survived, if any."""

    parts: int = 0
    model_calls: int = 0
    verdicts: list[Verdict] = field(default_factory=list)
    chosen: Answer | None = None
    whole_scan_reason: str | None = None
    """Why the combined windows failed on the whole scan, when they did."""
    chains: list[dict] = field(default_factory=list)
    snap_rescues: int = 0
    budget_reached: bool = False


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


def _accepted(verdict: Verdict, room: SceneGraph, layout: SceneGraph) -> Answer:
    """The moves that produce the snapped layout the verdict scored, not the model's raw request."""
    return Answer(verdict, moves_between(room, layout), snapped=verdict.snapped_meters > 0)


def _answer_of(attempt: Attempt, room: SceneGraph) -> Answer | None:
    if not attempt.accepted:
        return None
    if attempt.layout is None:
        raise ValueError("an accepted attempt arrived without the layout it scored")
    return _accepted(attempt.verdict, room, attempt.layout)


def _ask_part(part: Part, ask: Ask, result: Search, provider: str,
              progress: Callable[[str, str | None], None], index: int) -> Answer | None:
    prompt = openrouter_prompt_messages if provider == "openrouter" else prompt_messages
    messages = prompt(part.graph, part.checker)
    rounds: list[dict] = []
    chain = {"window": index, "window_graph_hash": graph_hash(part.graph),
             "prompt_messages": messages, "rounds": rounds}
    result.chains.append(chain)
    for round_index in range(1, MAX_ROUNDS + 1):
        if round_index == 1:
            progress("asking_model", None)
        try:
            completions = ask(messages)
        except BudgetReached:
            result.budget_reached = True
            return None
        result.model_calls += 1
        text = completions[0] if completions else ""
        progress("checking", None)
        attempt = diagnose(text, part.graph, part.checker)
        result.verdicts.append(attempt.verdict)
        answer = _answer_of(attempt, part.graph)
        if answer is not None and answer.snapped:
            result.snap_rescues += 1
        feedback = None if answer is not None or round_index == MAX_ROUNDS else feedback_message(attempt)
        rounds.append({"round": round_index, "proposal": text, "category": attempt.category,
                                "notes": list(attempt.notes), "feedback": feedback,
                                "verdict": attempt.verdict.as_dict(), "snapped": bool(answer and answer.snapped)})
        if answer is not None:
            return answer
        if feedback:
            progress("trying_again", attempt.category)
            messages = [*messages, {"role": "assistant", "content": text},
                        {"role": "user", "content": feedback}]
    return None


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
    judged = judge(_as_completion(moves), plan.graph, checker)
    if judged.layout is None:
        return Answer(judged.verdict, moves)
    return _accepted(judged.verdict, plan.graph, judged.layout)


def _whole_scan_choice(plan: ScanPlan, checker: TrainingChecker, answers: list[Answer], search: Search) -> None:
    combined = on_whole_scan(plan, checker, combined_moves(answers))
    if combined.verdict.gate_accepts:
        combined.snapped = combined.snapped or any(answer.snapped for answer in answers)
        search.chosen = combined
        return
    search.whole_scan_reason = combined.verdict.reason
    if len(answers) < 2:
        return
    for answer in sorted(answers, key=lambda a: a.verdict.reward, reverse=True):
        alone = on_whole_scan(plan, checker, answer.moves)
        if alone.verdict.gate_accepts:
            alone.snapped = alone.snapped or answer.snapped
            search.chosen = alone
            return


def search(plan: ScanPlan, checker: TrainingChecker, problems: list[Finding], ask: Ask,
           provider: str = "fireworks", progress: Callable[[str, str | None], None] | None = None) -> Search:
    """Ask each window in a feedback chain, then validate the joined layout on the whole scan."""
    result = Search()
    parts = parts_to_ask(plan, checker, problems)
    result.parts = len(parts)
    answers = []
    report = progress or (lambda phase, reason: None)
    for index, part in enumerate(parts):
        answer = _ask_part(part, ask, result, provider, report, index)
        if answer is not None:
            answers.append(answer)
        if result.budget_reached:
            break
    if not answers:
        return result
    if plan.small:
        result.chosen = answers[0]
        return result
    _whole_scan_choice(plan, checker, answers, result)
    return result
