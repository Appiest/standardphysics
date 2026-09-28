"""A language model fixes the whole layout turn by turn, picking from the menu of legal moves each time.

Set SP_LOOP_MODEL_URL and SP_LOOP_MODEL (an OpenAI-compatible server, such as
the Fireworks fine-tune behind scripts/finetune/fireworks_chat_server.py) and
SP_LOOP_MODEL_LABEL (what the owner's button calls it). Each turn builds the
menu for every problem still left, asks the model, applies its pick and
re-checks, for at most MODEL_LOOP_TURNS turns, each menu built within MENU_SECONDS. Only furniture options are
offered, because the owner's plan can only show furniture moves. Nothing is
saved: the stream ends with every move the loop made, for the owner to open
in the plan and keep or not.

A preview, like the single proposal: the menu uses the training checker, which
treats scan geometry marked "needs another look" as measured.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

from standardphysics_agents.fix.budget import deadline_in
from standardphysics_agents.training.checker import TrainingChecker
from standardphysics_agents.training.edits import apply_edits, node_moves, parse_edits
from standardphysics_agents.training.menu import MenuLimits, build_menu, menu_messages, resolve
from standardphysics_agents.training.owner import WishBook, stated_book
from standardphysics_contracts import (
    Finding,
    ModelLoopEvent,
    ModelLoopInfo,
    ModelLoopRequest,
    NodeMove,
    SceneGraph,
    Vec3,
)

from .db import Database
from .model_chooser import MENU_SECONDS, ModelChooser, furniture_only
from .proposals import fix_inputs, owner_wishes_of, space_typology_of
from .stages import Stages

MODEL_LOOP_TURNS = 5
LOOP_ENVIRONMENT = "SP_LOOP_"


def loop_chooser() -> ModelChooser | None:
    return ModelChooser.from_environment(LOOP_ENVIRONMENT)


def loop_info() -> ModelLoopInfo:
    chooser = loop_chooser()
    return ModelLoopInfo(available=chooser is not None, label=chooser.label if chooser else "")


def _combined(moves: dict[uuid.UUID, NodeMove], added: list[NodeMove]) -> dict[uuid.UUID, NodeMove]:
    """Each piece's total move from the starting layout; a later turn's move adds to an earlier one."""
    total = dict(moves)
    for move in added:
        before = total.get(move.node_id)
        if before is None:
            total[move.node_id] = move
            continue
        total[move.node_id] = NodeMove(node_id=move.node_id, delta_translation=Vec3(
            x=before.delta_translation.x + move.delta_translation.x,
            y=before.delta_translation.y + move.delta_translation.y,
            z=before.delta_translation.z + move.delta_translation.z,
        ), delta_rotation_z_degrees=before.delta_rotation_z_degrees + move.delta_rotation_z_degrees)
    return total


def _titles(problems: list[Finding]) -> list[str]:
    """Each open problem's title once, in the owner's words."""
    return list(dict.fromkeys(problem.title for problem in problems))


@dataclass
class ModelLoop:
    """One loop's state: the layout so far, every move made, and what to tell the model next turn."""

    start: SceneGraph
    checker: TrainingChecker
    stated: WishBook
    current: SceneGraph | None = None
    moves: dict = field(default_factory=dict)
    last: dict | None = None
    menu: object = None
    stop: str = ""

    def __post_init__(self) -> None:
        self.current = self.current or self.start

    def open_problems(self) -> list[Finding]:
        return self.checker.fixable_problems(self.checker.assess(self.current))

    def fixable_left(self) -> int:
        return len(self.open_problems())

    def next_messages(self) -> list[dict] | None:
        """The prompt for the next turn, or None when there is nothing left the menu can offer."""
        if self.fixable_left() == 0:
            self.stop = "Every problem furniture can fix is fixed."
            return None
        limits = MenuLimits(deadline=deadline_in(MENU_SECONDS))
        self.menu = furniture_only(build_menu(self.current, self.checker, stated=self.stated, limits=limits))
        if not self.menu.options:
            self.stop = "The menu has no move left for what remains."
            return None
        return menu_messages(self.current, self.checker, self.menu, self.last)

    def take(self, turn: int, reply: str) -> ModelLoopEvent:
        resolution = resolve(reply, self.current, self.menu, self.checker.pinned)
        edits = parse_edits(resolution.completion)
        added = node_moves(edits) if edits else []
        if added:
            self.current = apply_edits(self.current, edits)
            self.moves = _combined(self.moves, added)
        else:
            self.stop = "The model chose nothing it could use."
        open_problems = self.open_problems()
        self.last = {**resolution.as_dict(), "fixable_left": len(open_problems)}
        picked = [self.menu.picked_in_owner_words(number) for number in resolution.applied]
        return ModelLoopEvent(kind="turn", turn=turn, picked=picked, why=self.menu.in_owner_words(resolution.why),
                              fixable_left=len(open_problems), working_on=_titles(open_problems))


def _events(stages: Stages, graph: SceneGraph, scenario, chooser: ModelChooser, typology,
            wishes) -> Iterator[ModelLoopEvent]:
    """Turns until the room is clear, the menu runs dry, the model picks nothing, or the turns run out."""
    with stages.locked():
        loop = ModelLoop(graph, stages.menu_checker(graph, scenario, typology), stated_book(graph, list(wishes)))
        open_problems = loop.open_problems()
    yield ModelLoopEvent(kind="started", fixable_left=len(open_problems), working_on=_titles(open_problems),
                         turns_at_most=MODEL_LOOP_TURNS, message=f"{chooser.label} is looking at your shop.")
    for turn in range(1, MODEL_LOOP_TURNS + 1):
        with stages.locked():
            messages = loop.next_messages()
        if messages is None:
            break
        reply = chooser.ask(messages)
        with stages.locked():
            event = loop.take(turn, reply)
        yield event
        if loop.stop:
            break
    with stages.locked():
        left = loop.fixable_left()
    explanation = stages.explain(graph, loop.current, scenario, wishes) if loop.moves else None
    yield ModelLoopEvent(kind="finished", moves=list(loop.moves.values()), explanation=explanation, fixable_left=left,
                         message=loop.stop or f"Stopped after {MODEL_LOOP_TURNS} turns.")


def _line(event: ModelLoopEvent) -> str:
    return json.dumps(event.model_dump(mode="json")) + "\n"


def stream_model_loop(database: Database, stages: Stages, scan_id: uuid.UUID, body: ModelLoopRequest) -> Iterator[str]:
    """NDJSON lines: started with the fixable count, one line per turn, then finished with every move, or failed."""
    chooser = loop_chooser()
    if chooser is None:
        yield _line(ModelLoopEvent(kind="failed", message="No model is set up to run the loop."))
        return
    graph, scenario, _ = fix_inputs(database, scan_id, body.base_revision)
    wishes, typology = owner_wishes_of(database, scan_id), space_typology_of(database, scan_id)
    try:
        yield from (_line(event) for event in _events(stages, graph, scenario, chooser, typology, wishes))
    except OSError as error:
        yield _line(ModelLoopEvent(kind="failed", message=f"Unable to reach {chooser.label}: {error}. Try again."))
