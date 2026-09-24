"""The checker's feedback in plain words, the open floor hint, and the proposal loop."""

import json

import numpy as np
from feedback_eval import chain_worst_case_tokens, stratified_subset
from feedback_loop import (
    Attempt,
    Chain,
    explain_constraints,
    feedback_message,
    gate_category,
    largest_empty_rectangle,
    open_floor_rectangles,
    run_chains,
    with_open_floor,
)
from feedback_metrics import arm_metrics
from standardphysics_agents.training import Verdict
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_fixtures.shop import node_id


def _node(name, kind, label, centre, dims, movable):
    return SceneNode(id=node_id(name), kind=kind, label=label, raw_category=kind,
                     dimensions=Vec3(x=dims[0], y=dims[1], z=dims[2]), transform=Mat4.translation(*centre),
                     movable=movable)


def _room():
    floor = _node("fb_floor", "floor", "Floor", (0.0, 0.0, 0.0), (6.0, 6.0, 0.01), False)
    table = _node("fb_table", "object", "Table", (0.0, 0.0, 0.375), (1.2, 0.8, 0.75), False)
    chair = _node("fb_chair", "object", "Chair", (2.0, 0.0, 0.45), (0.45, 0.45, 0.9), True)
    return SceneGraph(scan_id=node_id("fb_room"), nodes=[floor, table, chair]), table, chair


def _move(piece, dx, dy=0.0):
    return json.dumps({"moves": [{"node_id": str(piece.id), "dx": dx, "dy": dy, "rotation_degrees": 0}]})


def _overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class TestFeedbackMessage:
    def test_a_collision_names_both_pieces_and_where_they_meet(self):
        room, table, chair = _room()
        kind, notes = explain_constraints(_move(chair, -2.0), room)
        assert kind == "collided"
        assert f"Chair ({chair.id})" in notes[0]
        assert "the fixed Table at [0.0, 0.0]" in notes[0]
        assert "would end at [0.0, 0.0]" in notes[0]

    def test_a_piece_carried_too_far_is_told_the_limit(self):
        room, _, chair = _room()
        kind, notes = explain_constraints(_move(chair, 0.0, 2.0), room)
        assert kind == "moved_too_far"
        assert "2.00 m from where the scan found it; the limit is 1.52 m" in notes[0]

    def test_the_message_lists_every_reason_and_asks_again_from_the_original_room(self):
        attempt = Attempt(Verdict(0.0), "collided", ("Chair hit the table.", "Lamp left the floor."))
        message = feedback_message(attempt)
        assert "- Chair hit the table.\n- Lamp left the floor." in message
        assert "still exactly as first described" in message
        assert message.endswith("as JSON only in the same format.")

    def test_gate_reasons_map_to_one_category_with_new_problems_first(self):
        assert gate_category(["nothing measurable changed", "new problem: The path is too narrow"]) == "new_problem"
        assert gate_category(["improvement within measurement noise"]) == "noise"
        assert gate_category(["nothing measurable changed"]) == "nothing_changed"
        assert gate_category(["something else"]) == "gate_other"


class TestOpenFloor:
    def test_the_largest_empty_rectangle_in_a_grid(self):
        free = np.ones((4, 6), dtype=bool)
        free[:, 2] = False
        assert largest_empty_rectangle(free) == (12, 0, 4, 3, 6)

    def test_rectangles_avoid_furniture_stay_on_the_floor_and_do_not_overlap(self):
        room, _, _ = _room()
        rects = open_floor_rectangles(room, count=15)
        table_box, chair_box = [-0.6, -0.4, 0.6, 0.4], [1.775, -0.225, 2.225, 0.225]
        assert 1 < len(rects) <= 15
        areas = [(r[2] - r[0]) * (r[3] - r[1]) for r in rects]
        assert areas == sorted(areas, reverse=True)
        for index, rect in enumerate(rects):
            assert -3.0 <= rect[0] < rect[2] <= 3.0 and -3.0 <= rect[1] < rect[3] <= 3.0
            assert not _overlaps(rect, table_box) and not _overlaps(rect, chair_box)
            assert not any(_overlaps(rect, other) for other in rects[index + 1:])

    def test_the_hint_is_added_to_the_room_description_and_nothing_else_changes(self):
        room, _, _ = _room()
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": '{"problems":[]}'}]
        hinted = with_open_floor(messages, room, count=3)
        view = json.loads(hinted[1]["content"])
        assert hinted[0] == messages[0]
        assert view["problems"] == [] and len(view["open_floor"]) == 3


class TestLoop:
    def test_a_chain_stops_at_the_first_accepted_proposal_and_feeds_back_each_refusal(self):
        answers = {"a": ["bad", "bad", "good", "never"], "b": ["bad"] * 4}
        calls = []

        def propose(conversations):
            calls.append(len(conversations))
            return [answers[conv[0]["content"]][len(conv) // 2] for conv in conversations]

        def judge(completion, variant):
            accepted = completion == "good"
            return Attempt(Verdict(0.5 if accepted else 0.0, gate_accepts=accepted),
                           "accepted" if accepted else "collided", () if accepted else ("hit",))

        chains = [Chain(name, [{"role": "user", "content": name}]) for name in ("a", "b")]
        run_chains(chains, propose, judge, rounds=4)
        assert calls == [2, 2, 2, 1]
        assert chains[0].accepted_at == 3 and len(chains[0].rounds) == 3
        assert chains[1].accepted_at is None and chains[1].rounds[-1]["feedback"] is None
        assert chains[1].messages[-1]["content"].startswith("The application measured that proposal")
        assert len(chains[1].messages) == 7

    def test_metrics_keep_the_best_accepted_proposal_and_count_rounds(self):
        def attempt(round_index, accepted, reward=0.0, left=1):
            return {"round": round_index, "category": "accepted" if accepted else "collided", "reward": reward,
                    "gate_accepts": accepted, "parsed": True, "hard_constraints_pass": accepted,
                    "fixable_left": left, "shortfall_recovered": reward, "usability": 1.0 if accepted else None}

        records = [
            {"ceiling_fixable": True, "ceiling_all_clear": True,
             "rounds": [attempt(1, False), attempt(2, True, 0.4), attempt(3, True, 0.9, left=0)]},
            {"ceiling_fixable": False, "ceiling_all_clear": False, "rounds": [attempt(i, False) for i in range(1, 5)]},
        ]
        metrics = arm_metrics(records)
        assert metrics["accepted_rooms"] == 1 and metrics["accepted_of_ceiling"] == 1.0
        assert metrics["cleared_rooms"] == 1 and metrics["mean_rounds_to_accept"] == 2
        assert metrics["mean_recovered_among_accepted"] == 0.9 and metrics["mean_room_reward"] == 0.45
        assert metrics["rejections_by_round"]["1"] == {"collided": 2}


def test_the_worst_case_counts_every_round_growing():
    prefill, sample = chain_worst_case_tokens([1000], rounds=3, max_sample=100)
    assert prefill == 3000 + 500 * 3 and sample == 300


def test_the_subset_keeps_the_fixable_share():
    variants = [f"v{i}" for i in range(10)]
    subset = stratified_subset(variants, {v: i < 6 for i, v in enumerate(variants)}, size=5)
    assert len(subset) == 5 and sum(1 for v in subset if int(v[1:]) < 6) == 3
