"""Lane C's whole loop on one layout, reported pass by pass."""

from __future__ import annotations

import uuid

from standardphysics_agents import LoopStep
from standardphysics_contracts import LoopPass, LoopRequest, LoopResult, NodeMove, Vec3

from .db import Database
from .proposals import fix_inputs
from .stages import Stages


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


def run(database: Database, stages: Stages, scan_id: uuid.UUID, body: LoopRequest) -> LoopResult:
    graph, scenario, _ = fix_inputs(database, scan_id, body.base_revision)
    decided_by, steps = stages.loop(graph, scenario)
    passes = [_to_pass(step) for step in steps]
    return LoopResult(
        base_revision=body.base_revision,
        decided_by=decided_by,
        passes=passes,
        moves=combine_moves([move for loop_pass in passes for move in loop_pass.moves]),
    )
