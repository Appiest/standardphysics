"""Lane C's whole loop on one layout, reported pass by pass: all at once, or as each pass finishes."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Iterator

from standardphysics_agents import LoopStep
from standardphysics_contracts import (
    LoopEvent,
    LoopFailed,
    LoopFinished,
    LoopPass,
    LoopPassFinished,
    LoopRequest,
    LoopResult,
    LoopStarted,
    NodeMove,
    Vec3,
)

from .db import Database
from .proposals import fix_inputs
from .stages import Stages

log = logging.getLogger(__name__)

STOPPED_PARTWAY = "Unable to finish the loop. Nothing was changed, so you can try again."


def _kept_moves(step: LoopStep) -> list[NodeMove]:
    kept = step.result.gate is not None and step.result.gate.accepted and step.result.proposal is not None
    return list(step.result.proposal.moves) if kept else []


def _to_pass(step: LoopStep) -> LoopPass:
    gate = step.result.gate
    return LoopPass(
        number=step.pass_number,
        action=step.action,
        problems=len(step.assessment.problems),
        questions=len(step.assessment.questions),
        message=step.message,
        kept=None if gate is None else gate.accepted,
        inches_short_before=None if gate is None else gate.shortfall_before,
        inches_short_after=None if gate is None else gate.shortfall_after,
        moves=_kept_moves(step),
        question=step.result.question,
    )


def combine_moves(moves: list[NodeMove]) -> list[NodeMove]:
    """One move per piece. Lane C moves a piece from where it stands, so kept moves add up."""
    combined: dict[uuid.UUID, NodeMove] = {}
    for move in moves:
        before = combined.get(move.node_id)
        if before is None:
            combined[move.node_id] = move
            continue
        combined[move.node_id] = NodeMove(
            node_id=move.node_id,
            delta_translation=Vec3(
                x=before.delta_translation.x + move.delta_translation.x,
                y=before.delta_translation.y + move.delta_translation.y,
                z=before.delta_translation.z + move.delta_translation.z,
            ),
            delta_rotation_z_degrees=before.delta_rotation_z_degrees + move.delta_rotation_z_degrees,
        )
    return list(combined.values())


def _start(database: Database, stages: Stages, scan_id: uuid.UUID, body: LoopRequest) -> tuple[str, Iterable[LoopStep]]:
    graph, scenario, _ = fix_inputs(database, scan_id, body.base_revision)
    return stages.loop(graph, scenario)


def _result(body: LoopRequest, decided_by: str, passes: list[LoopPass]) -> LoopResult:
    return LoopResult(
        base_revision=body.base_revision,
        decided_by=decided_by,
        passes=passes,
        moves=combine_moves([move for loop_pass in passes for move in loop_pass.moves]),
    )


def run(database: Database, stages: Stages, scan_id: uuid.UUID, body: LoopRequest) -> LoopResult:
    decided_by, steps = _start(database, stages, scan_id, body)
    return _result(body, decided_by, [_to_pass(step) for step in steps])


def stream(database: Database, stages: Stages, scan_id: uuid.UUID, body: LoopRequest) -> Iterator[str]:
    """Refuses a missing layout or route before the response starts, then yields one JSON line per event."""
    decided_by, steps = _start(database, stages, scan_id, body)
    return _event_lines(body, decided_by, steps)


def _event_lines(body: LoopRequest, decided_by: str, steps: Iterable[LoopStep]) -> Iterator[str]:
    yield _line(LoopStarted(base_revision=body.base_revision, decided_by=decided_by))
    passes: list[LoopPass] = []
    try:
        for step in steps:
            passes.append(_to_pass(step))
            yield _line(LoopPassFinished(loop_pass=passes[-1]))
    except Exception:
        # The status line is already sent, so the failure has to travel as an event.
        log.exception("the streamed loop failed after %d passes", len(passes))
        yield _line(LoopFailed(error=STOPPED_PARTWAY))
        return
    yield _line(LoopFinished(result=_result(body, decided_by, passes)))


def _line(event: LoopStarted | LoopPassFinished | LoopFinished | LoopFailed) -> str:
    return LoopEvent(event).model_dump_json() + "\n"
