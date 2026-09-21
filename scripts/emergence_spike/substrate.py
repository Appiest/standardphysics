"""A minimal substrate over a real scan, plus an evaluator for model-written expressions.

Regions are nameless. The operators are geometry only. Nothing here knows what
a chair is, and nothing defines 'rests on' or 'balanced' — the point of the
experiment is whether a model can write those itself.
"""
import json
import math
import pathlib

from standardphysics_pipeline.ingest import parse_room_json

ROOT = pathlib.Path(__file__).resolve().parents[2]

GRAVITY = [0.0, 0.0, -1.0]  # measured up-axis of the scan is +z


def load(name):
    graph = parse_room_json(json.loads(ROOT.joinpath("datasets", "phone", name, "room.json").read_text()))
    regions = {}
    truth = {}
    for index, node in enumerate(graph.nodes):
        rid = f"r{index}"
        p, d = node.transform.position, node.dimensions
        regions[rid] = {
            "id": rid,
            "centroid": [p.x, p.y, p.z],
            "extent": [d.x, d.y, d.z],
            "min": [p.x - d.x / 2, p.y - d.y / 2, p.z - d.z / 2],
            "max": [p.x + d.x / 2, p.y + d.y / 2, p.z + d.z / 2],
        }
        truth[rid] = node.label  # held back from the model, used only to score
    return regions, truth


def _axis(along):
    return {"gravity": GRAVITY, "x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}[along]


def op_gap(a, b, along="gravity"):
    """Signed distance from a's near face to b's far face along a direction.

    Along gravity: how far a's underside sits above b's top. Negative means
    they interpenetrate.
    """
    axis = _axis(along)
    index = max(range(3), key=lambda i: abs(axis[i]))
    if axis[index] < 0:
        return a["min"][index] - b["max"][index]
    return b["min"][index] - a["max"][index]


def op_footprint_overlap(a, b):
    """Fraction of a's ground footprint lying within b's."""
    area = 0.0
    for i in (0, 1):
        low, high = max(a["min"][i], b["min"][i]), min(a["max"][i], b["max"][i])
        span = max(0.0, high - low)
        area = span if i == 0 else area * span
    own = (a["max"][0] - a["min"][0]) * (a["max"][1] - a["min"][1])
    return area / own if own > 0 else 0.0


def op_volume(a):
    return (a["max"][0] - a["min"][0]) * (a["max"][1] - a["min"][1]) * (a["max"][2] - a["min"][2])


def op_horizontal_distance(a, b):
    return math.dist(a["centroid"][:2], b["centroid"][:2])


def op_centroid(a, axis=2):
    return a["centroid"][axis]


def op_extent(a, axis=2):
    return a["extent"][axis]


def op_bottom(a):
    return a["min"][2]


def op_top(a):
    return a["max"][2]


OPS = {
    "gap": op_gap, "footprint_overlap": op_footprint_overlap, "volume": op_volume,
    "horizontal_distance": op_horizontal_distance, "centroid": op_centroid,
    "extent": op_extent, "bottom": op_bottom, "top": op_top,
}


class Refused(ValueError):
    pass


def value(node, env, regions):
    if isinstance(node, (int, float)):
        return float(node)
    if not isinstance(node, dict):
        raise Refused(f"not an expression: {node!r}")
    if "op" not in node:
        raise Refused(f"expression with no op: {node!r}")
    name = node["op"]
    if name not in OPS:
        raise Refused(f"unknown operator {name!r}")
    kwargs = {}
    for key, raw in node.items():
        if key == "op":
            continue
        if key in ("a", "b"):
            kwargs[key] = regions[env[raw]] if isinstance(raw, str) and raw.startswith("$") else regions[raw]
        else:
            kwargs[key] = raw
    return float(OPS[name](**kwargs))


def holds(node, env, regions):
    if not isinstance(node, dict) or len(node) != 1:
        raise Refused(f"not a predicate: {node!r}")
    key, body = next(iter(node.items()))
    if key == "and":
        return all(holds(item, env, regions) for item in body)
    if key == "or":
        return any(holds(item, env, regions) for item in body)
    if key == "not":
        return not holds(body, env, regions)
    if key == "exists":
        bound = body["as"]
        return any(
            holds(body["where"], {**env, bound: other}, regions)
            for other in regions
            if other != env.get("$r")
        )
    if key in ("lt", "gt", "lte", "gte"):
        left, right = value(body[0], env, regions), value(body[1], env, regions)
        return {"lt": left < right, "gt": left > right, "lte": left <= right, "gte": left >= right}[key]
    if key in ("eq", "neq"):
        left, right = body
        if isinstance(left, str) and isinstance(right, str):
            same = env.get(left, left) == env.get(right, right)
            return same if key == "eq" else not same
        same = abs(value(left, env, regions) - value(right, env, regions)) < 1e-9
        return same if key == "eq" else not same
    if key == "abs_lt":
        return abs(value(body[0], env, regions)) < value(body[1], env, regions)
    raise Refused(f"unknown predicate {key!r}")


def select(expression, regions):
    """Every region for which the model's predicate holds."""
    return [rid for rid in regions if holds(expression, {"$r": rid}, regions)]
