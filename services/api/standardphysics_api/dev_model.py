"""Development only: answers for the local stand-in model behind SP_REARRANGE_FAKE_MODEL.

It reads the same prompt the real model gets and slides the movable pieces a
problem names straight away from where the problem was measured, by four
different distances. The checker then judges these like any model answer, so
the page can be tried end to end with no deployment and no paid call.
"""

from __future__ import annotations

import json
import math

DISTANCES_METERS = (0.15, 0.3, 0.45, 0.6)


def _away(piece: dict, problem_at: list[float], distance: float) -> dict:
    dx, dy = piece["center"][0] - problem_at[0], piece["center"][1] - problem_at[1]
    length = math.hypot(dx, dy) or 1.0
    step = min(distance, piece["travel_left_m"])
    return {"node_id": piece["id"], "dx": round(dx / length * step, 3), "dy": round(dy / length * step, 3),
            "rotation_degrees": 0}


def _pieces_to_move(room: dict) -> tuple[list[dict], list[float]] | None:
    movable = {piece["id"]: piece for piece in room["movable_objects"]}
    for problem in room["problems"]:
        named = [movable[item["id"]] for item in problem["involves"] if item["id"] in movable]
        if named and problem["at"]:
            return named, problem["at"]
    return None


def nudges_from_prompt(messages: list[dict]) -> list[str]:
    room = json.loads(messages[-1]["content"])
    found = _pieces_to_move(room)
    if found is None:
        return ['{"moves":[]}']
    pieces, at = found
    return [json.dumps({"moves": [_away(piece, at, distance) for piece in pieces]}) for distance in DISTANCES_METERS]
