import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from menu_data import MenuData, assistant_turn, menu_row
from standardphysics_agents.rules import VerificationLedger, load_pack
from standardphysics_agents.training import TrainingChecker
from standardphysics_contracts import Scenario, SceneGraph
from standardphysics_pipeline import PipelineMeasurements

FIXTURE = Path(__file__).resolve().parents[2] / "packages/agents/tests/fixtures/placement-room.json"


@pytest.fixture(scope="module")
def room_data():
    saved = json.loads(FIXTURE.read_text())
    pack, ledger = load_pack(), VerificationLedger()
    for rule in pack.rules:
        ledger = ledger.record(rule, verified_by="test suite, not a person")
    room = SceneGraph.model_validate(saved["graph"])
    checker = TrainingChecker(Scenario.model_validate(saved["scenario"]), rules=pack, ledger=ledger,
                              measure=PipelineMeasurements(), owner_layout=room)
    return SimpleNamespace(variants={"v": {"window_id": "w"}}, checker=lambda _: checker, graph=lambda _: room)


@pytest.fixture(scope="module")
def row(room_data):
    return menu_row(room_data, "v", "train")


def test_a_row_is_a_blind_menu_with_the_checkers_best_pick_as_its_answer(row):
    user = json.loads(row["messages"][1]["content"])
    assert "owner_wishes" not in user and all("breaks_wishes" not in option for option in user["options"])
    numbers = {option["number"] for option in row["options"]}
    assert set(row["target"]["choose"]) <= numbers and row["best_reward"] > 0
    assert row["best_reward"] == max(row["scores"].values())


def test_the_reward_resolves_a_reply_against_the_rows_own_menu(row, room_data):
    data = MenuData(rooms={"train": room_data}, rows={"v": row})
    answer = assistant_turn(row)["content"]
    assert data.score(answer, "v").reward == pytest.approx(row["best_reward"], abs=1e-3)
    assert data.score("no idea", "v").reward == 0.0
