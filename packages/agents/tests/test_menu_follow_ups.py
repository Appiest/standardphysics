"""A move refused only for the problem it brings, offered together with a move that clears that problem."""

import json
import uuid
from pathlib import Path

import pytest
from standardphysics_agents.fix import apply_moves
from standardphysics_agents.scenario_suggestion import suggest_scenario
from standardphysics_agents.training import TrainingChecker, score_completion
from standardphysics_agents.training.checker import trusted_where_moved
from standardphysics_agents.training.menu import build_menu
from standardphysics_contracts import NodeMove, Vec3
from standardphysics_pipeline import PipelineMeasurements
from standardphysics_pipeline.ingest import parse_room_json

LIVING_ROOM = Path(__file__).parents[3] / "datasets/replays/living-room/room.json"
SOFA = uuid.UUID("cfb5b6f7-5ffc-4dc2-abad-f4d2c0e91470")


@pytest.fixture(scope="module")
def sofa_in_the_doorway():
    """The living room scan with its sofa dragged a metre toward the door, as an owner might in the plan, checked
    with the shipped ledger as the owner's loop checks it."""
    scanned = parse_room_json(json.loads(LIVING_ROOM.read_text()))
    dragged = apply_moves(scanned, [NodeMove(node_id=SOFA, delta_translation=Vec3(x=1.0, y=0.0, z=0.0))])
    room = trusted_where_moved(scanned, dragged)
    checker = TrainingChecker(suggest_scenario(scanned), measure=PipelineMeasurements(),
                              owner_layout=room, scope="fittings", promoted=frozenset(), trust_unsure_geometry=False)
    return room, checker


def test_a_route_the_sofas_seal_is_offered_one_sofa_moved_and_then_the_other(sofa_in_the_doorway):
    room, checker = sofa_in_the_doorway
    sealed = [finding for finding in checker.fixable_problems(checker.assess(room))
              if finding.check_id == "route_clear_width" and finding.measured_inches is None]
    assert sealed
    menu = build_menu(room, checker)
    labels = {menu.problems[finding.id] for finding in sealed}
    clearing = [option for option in menu.options if labels & set(option.effect["clears"])]
    assert clearing, [option.wording for option in menu.options]
    for option in clearing:
        assert len(option.edits.moves) == 2 and "; " in option.wording
        verdict = score_completion(json.dumps(option.edits.model_dump(mode="json")), room, checker)
        assert verdict.hard_constraints_pass and verdict.gate_accepts
