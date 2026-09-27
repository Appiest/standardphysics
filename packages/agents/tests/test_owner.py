"""The owner in the loop, simulated from their own layout or typed at a terminal."""

import json
from pathlib import Path

import pytest
from standardphysics_agents.fix import apply_moves
from standardphysics_agents.training.owner import InteractiveOwner, SimulatedOwner, WishBook, stated_from
from standardphysics_agents.training.wishes import infer_wishes, stays_put
from standardphysics_contracts import NodeMove, SceneGraph, Vec3
from standardphysics_pipeline import PipelineMeasurements


@pytest.fixture(scope="module")
def room():
    data = json.loads((Path(__file__).parent / "fixtures/placement-room.json").read_text())
    return SceneGraph.model_validate(data["graph"])


@pytest.fixture(scope="module")
def measure():
    return PipelineMeasurements()


def _slide(graph, node_id, dx):
    return apply_moves(graph, [NodeMove(node_id=node_id, delta_translation=Vec3(x=dx, y=0.0, z=0.0))])


def _seat(room, measure):
    return next(wish for wish in infer_wishes(room, measure) if wish.kind == "with_table").subjects[0]


def test_the_owner_turns_down_a_change_that_breaks_their_layout_and_says_why(room, measure):
    owner = SimulatedOwner(room, measure)
    seat = _seat(room, measure)
    review = owner.review(room, _slide(room, seat, 1.0))
    assert not review.accepted and "stays at the" in review.said
    assert review.stated and review.stated[0].hard and review.about is room


def test_the_owner_accepts_a_change_that_keeps_their_wishes(room, measure):
    owner = SimulatedOwner(room, measure)
    assert owner.review(room, _slide(room, _seat(room, measure), 0.05)).accepted


def test_a_wish_the_scramble_already_broke_is_not_held_against_the_change(room, measure):
    owner = SimulatedOwner(room, measure)
    scrambled = _slide(room, _seat(room, measure), 1.0)
    assert owner.review(scrambled, _slide(scrambled, _seat(room, measure), 0.05)).accepted
    assert owner.kept_share(scrambled, scrambled) == 1.0
    assert owner.kept_share(room, scrambled) < 1.0


def test_the_wish_book_holds_changes_to_stated_wishes_only_when_asked(room, measure):
    book = WishBook()
    seat = room.by_id(_seat(room, measure))
    book.add(stays_put(seat), room)
    book.add(stays_put(seat), room)
    moved = _slide(room, seat.id, 0.2)
    assert len(book.entries) == 1
    assert book.broken(moved, measure, hard_only=True) == [stays_put(seat)]
    assert book.newly_broken(room, moved, measure) == [stays_put(seat)]


def test_typed_answers_state_locks_and_nearness_by_the_start_of_an_id(room, measure):
    chair = next(node for node in room.nodes if node.label == "Chair")
    table = next(node for node in room.nodes if node.label == "Table")
    wishes = stated_from(f"no, lock {str(chair.id)[:4]} and near {str(chair.id)[:4]} {str(table.id)[:4]} 30", room)
    assert [wish.kind for wish in wishes] == ["stays_put", "stays_near"]
    assert wishes[1].meters == pytest.approx(30 * 0.0254)


def test_a_person_at_the_terminal_can_say_yes_or_lock_a_piece(room):
    chair = next(node for node in room.nodes if node.label == "Chair")
    shown = []
    yes = InteractiveOwner(ask=lambda _: "y", show=shown.append).review(room, room, "Here is what changed.")
    no = InteractiveOwner(ask=lambda _: f"n lock {str(chair.id)[:6]}", show=shown.append).review(room, room)
    assert yes.accepted and shown[0] == "Here is what changed."
    assert not no.accepted and no.stated[0].kind == "stays_put" and no.about is room
