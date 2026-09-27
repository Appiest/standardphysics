"""One whiteboard per place on the walls, on the boards a real walk produced.

RoomPlan measured one of Moffitt's walls as overlapping pieces, and the same
photographed board landed on two of them thirty centimetres apart. The five
boards here are what the south walk's photos produced before the fold, recorded
in `datasets/replays/moffitt-regions/entrance-walk-whiteboards.json`.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest
from standardphysics_contracts import SceneNode
from standardphysics_pipeline.discovery.semantic_corrections import BOARD_APART, _one_board_per_place
from standardphysics_pipeline.occupancy import reads_as_wall

RECORDED = pathlib.Path(__file__).resolve().parents[3] / "datasets" / "replays" / "moffitt-regions" / "entrance-walk-whiteboards.json"

pytestmark = pytest.mark.skipif(not RECORDED.is_file(), reason="the recorded boards are not in this checkout")


@pytest.fixture(scope="module")
def boards() -> list[SceneNode]:
    return [SceneNode.model_validate(node) for node in json.loads(RECORDED.read_text())]


def centre(node: SceneNode) -> np.ndarray:
    return np.asarray(node.transform.position.as_tuple())


def test_the_same_board_on_two_pieces_of_one_wall_is_one_board(boards):
    kept = _one_board_per_place(boards)
    assert len(boards) == 5 and len(kept) == 4
    for index, one in enumerate(kept):
        assert all(np.linalg.norm(centre(one) - centre(other)) >= BOARD_APART for other in kept[index + 1:])


def test_the_board_kept_is_the_best_evidenced(boards):
    kept = {node.id for node in _one_board_per_place(boards)}
    for board in boards:
        if board.id in kept:
            continue
        twin = next(other for other in boards if other.id in kept and np.linalg.norm(centre(board) - centre(other)) < BOARD_APART)
        assert twin.attachment.identity_confidence >= board.attachment.identity_confidence


def test_a_board_is_broad_and_thin_enough_to_read_as_a_wall(boards):
    """Which is why a mounted thing must never be offered a board of its own."""
    assert any(reads_as_wall(board) for board in boards)
