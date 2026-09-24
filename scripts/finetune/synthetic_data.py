"""Build synthetic shop training rows while preserving the real multiroom held-out set."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import pathlib
import random
import uuid

from multiroom_data import (
    Progress,
    _append,
    _prompt_row,
    _rows,
    _write_json,
    checker_for,
    run_targets,
    target_quality,
)
from standardphysics_agents.fix import violations
from standardphysics_agents.training import scramble
from standardphysics_agents.training.scramble import LIGHT
from standardphysics_agents.training.windows import Window
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_fixtures.shop import CARD_READER_SIZE, build_graph, build_scenario
from standardphysics_pipeline import footprint, gap_between

NAMESPACE = uuid.UUID("ac88fb4c-5365-4c90-9851-70b998249886")
ROOM_TYPES = ("cafe", "boba tea shop", "bakery", "boutique", "small office")
DIMENSION_SOURCES = {
    "table": "standardphysics_fixtures.shop._furniture: 0.60 x 0.60 x 0.75 m",
    "chair": "standardphysics_fixtures.shop._furniture: 0.45 x 0.45 x 0.90 m",
    "display": "standardphysics_fixtures.shop._display_cases: 0.60 m deep, 0.90 m high",
    "counter": "standardphysics_fixtures.shop.COUNTER_LENGTH and COUNTER_HEIGHT",
    "point_of_sale": "standardphysics_fixtures.shop.CARD_READER_SIZE: 0.20 x 0.16 x 0.08 m",
    "wing_shelf": "IKEA BILLY 80 x 28 x 202 cm; https://www.ikea.com/us/en/p/billy-bookcase-white-00263850/",
}


def _position(node: SceneNode, x: float, y: float) -> SceneNode:
    z = node.transform.position.z
    return node.model_copy(update={"transform": Mat4.translation(x, y, z)})


def _new_node(room_name: str, name: str, kind: str, label: str, category: str,
              center: tuple[float, float, float], dimensions: tuple[float, float, float], movable=False) -> SceneNode:
    return SceneNode(id=uuid.uuid5(NAMESPACE, f"{room_name}:{name}"), kind=kind, label=label,
                     raw_category=category, dimensions=Vec3(x=dimensions[0], y=dimensions[1], z=dimensions[2]),
                     transform=Mat4.translation(*center), movable=movable)


def _wing(room_name: str) -> list[SceneNode]:
    return [
        _new_node(room_name, "wing_floor", "floor", "Floor", "floor", (4, 2, 0), (2, 4, 0.01)),
        _new_node(room_name, "wing_east", "wall", "Wall", "wall", (5, 2, 1.5), (0.1, 4, 3)),
        _new_node(room_name, "wing_north", "wall", "Wall", "wall", (4, 4, 1.5), (2, 0.1, 3)),
        _new_node(room_name, "wing_south", "wall", "Wall", "wall", (4, 0, 1.5), (2, 0.1, 3)),
        _new_node(room_name, "wing_shelf", "object", "Bookcase", "storage", (4.15, 2, 1.01),
                  (0.8, 0.28, 2.02)),
    ]


def _shop_nodes(room_name: str, room_type: str, rng: random.Random, l_shape: bool) -> tuple[list[SceneNode], dict]:
    template = build_graph()
    id_map = {node.id: uuid.uuid5(NAMESPACE, f"{room_name}:{node.id}") for node in template.nodes}
    nodes = []
    for node in template.nodes:
        if l_shape and node.label == "Wall" and node.transform.position.x == 3:
            node = node.model_copy(update={"dimensions": Vec3(x=0.1, y=4, z=3)})
            node = _position(node, 3, -2)
        if node.raw_category in ("table", "chair"):
            node = _position(node, node.transform.position.x + rng.uniform(-0.04, 0.04),
                             node.transform.position.y + rng.uniform(-0.04, 0.04))
        label = node.label
        if label == "Ordering counter" and room_type == "small office":
            label = "Reception desk"
        if label == "Display case" and room_type in ("boutique", "small office"):
            label = "Display shelf"
        nodes.append(node.model_copy(update={"id": id_map[node.id], "label": label,
                                     "parent_id": id_map.get(node.parent_id)}))
    if l_shape:
        nodes.extend(_wing(room_name))
    if room_type != "small office":
        nodes.append(_new_node(room_name, "pos", "object", "Point of sale", "storage",
                               (0, 3.6, template.by_id(next(n.id for n in template.nodes
                                                              if n.label == "Ordering counter")).dimensions.z
                                + CARD_READER_SIZE[2] / 2), CARD_READER_SIZE))
    return nodes, id_map


def _furniture_valid(graph: SceneGraph) -> bool:
    pieces = [node for node in graph.nodes if node.movable]
    if violations(graph, graph):
        return False
    if any(abs(node.transform.position.z - node.dimensions.z / 2) > 0.02 for node in pieces):
        return False
    return all(gap_between(footprint(left), footprint(right)) > 0
               for index, left in enumerate(pieces) for right in pieces[index + 1:])


def make_room(index: int) -> Window:
    room_name = f"synthetic-{index:04d}"
    room_type = ROOM_TYPES[index % len(ROOM_TYPES)]
    l_shape = index % 2 == 1
    for attempt in range(40):
        nodes, id_map = _shop_nodes(room_name, room_type, random.Random(index * 101 + attempt), l_shape)
        graph = SceneGraph(scan_id=uuid.uuid5(NAMESPACE, room_name), nodes=nodes)
        if not _furniture_valid(graph):
            continue
        scenario = build_scenario()
        stops = [stop.model_copy(update={"anchor_node_id": id_map.get(stop.anchor_node_id)})
                 for stop in scenario.stops]
        scenario = scenario.model_copy(update={"name": f"Visit a {room_type}", "stops": stops})
        return Window(window_id=f"{room_name}:whole", scan_id=str(graph.scan_id), centre=None, graph=graph,
                      scenario=scenario, route="scan")
    raise RuntimeError(f"could not generate a collision-free room for {room_name}")


def run_rooms(run: pathlib.Path, count: int) -> None:
    output = run / "windows.jsonl"
    done = {row["window_id"] for row in _rows(output)}
    for index in range(count):
        window = make_room(index)
        if window.window_id in done:
            continue
        _append(output, window.as_dict())
        _write_json(run / "progress.json", {"stage": "rooms", "done": index + 1, "total": count})
        print(f"room {index + 1}/{count}", flush=True)


def _scramble_task(row: dict) -> list[dict]:
    window = Window.from_dict(row)
    checker = checker_for(window)
    made = scramble(window.graph, checker, 3, seed=int(window.window_id[10:14]), how=LIGHT)
    return [{"variant_id": f"{window.window_id}:{variant.name}", "window_id": window.window_id,
             "scan_id": window.scan_id, "name": variant.name, "fixable": list(variant.fixable),
             "graph": variant.graph.model_dump(mode="json")} for variant in made]


def run_scrambles(run: pathlib.Path, workers: int) -> None:
    output = run / "variants.jsonl"
    done = {row["window_id"] for row in _rows(output)}
    windows = _rows(run / "windows.jsonl")
    todo = [row for row in windows if row["window_id"] not in done]
    with multiprocessing.get_context("spawn").Pool(workers) as pool:
        for rows, window in zip(pool.imap(_scramble_task, todo), todo):
            for row in rows:
                _append(output, row)
            if not rows:
                _append(output, {"variant_id": None, "window_id": window["window_id"]})
            done.add(window["window_id"])
            _write_json(run / "progress.json", {"stage": "scrambles", "done": len(done), "total": len(windows)})
            print(f"scramble {len(done)}/{len(windows)} variants={len(rows)}", flush=True)


def run_dataset(run: pathlib.Path, real: pathlib.Path) -> None:
    if (run / "report.json").exists():
        return
    synthetic_windows = _rows(run / "windows.jsonl")
    synthetic_variants = [row for row in _rows(run / "variants.jsonl") if row["variant_id"]]
    targets = {row["variant_id"]: row for row in _rows(run / "targets.jsonl")}
    real_heldout = _rows(real / "dataset/heldout.jsonl")
    real_ids = {row["variant"] for row in real_heldout}
    real_variants = [row for row in _rows(real / "variants.jsonl") if row["variant_id"] in real_ids]
    real_windows = [row for row in _rows(real / "windows.jsonl")
                    if row["window_id"] in {variant["window_id"] for variant in real_variants}]
    windows = {row["window_id"]: Window.from_dict(row) for row in synthetic_windows}
    sft, rl = [], []
    for variant in synthetic_variants:
        row = _prompt_row(variant, windows[variant["window_id"]], targets.get(variant["variant_id"]))
        rl.append({key: value for key, value in row.items() if key != "target"})
        if "target" in row:
            sft.append({"messages": [*row["messages"], {"role": "assistant", "content": row["target"]}],
                        "variant": row["variant"], "window": row["window"]})
    dataset = run / "dataset"
    dataset.mkdir(exist_ok=True)
    for name, rows in (("sft", sft), ("rl", rl), ("heldout", real_heldout)):
        (dataset / f"{name}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    for name, synthetic, real_rows in (("windows", synthetic_windows, real_windows),
                                       ("variants", _rows(run / "variants.jsonl"), real_variants)):
        (run / f"{name}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in [*synthetic, *real_rows]))
    real_report = json.loads((real / "report.json").read_text())
    _write_json(run / "report.json", {"windows": [{"window_id": row["window_id"], "scan": "synthetic"}
                                                  for row in synthetic_windows] +
                                                 [row for row in real_report["windows"] if row["window_id"]
                                                  in {window["window_id"] for window in real_windows}],
                                        "split": {"totals": {"train": len(synthetic_variants),
                                                             "heldout": len(real_heldout)},
                                                  "by_scan": {}, "held_out": real_report["split"]["held_out"]},
                                        "variants": {"total": len(synthetic_variants) + len(real_variants)},
                                        "targets": target_quality(list(targets.values()))})
    _write_json(run / "progress.json", {"stage": "dataset", "done": True,
                                         "rooms": len(synthetic_windows), "sft": len(sft), "rl": len(rl),
                                         "heldout": len(real_heldout)})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("rooms", "scrambles", "targets", "dataset"))
    parser.add_argument("--run", type=pathlib.Path, required=True)
    parser.add_argument("--real", type=pathlib.Path)
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    args.run.mkdir(parents=True, exist_ok=True)
    if args.stage == "rooms":
        run_rooms(args.run, args.count)
    elif args.stage == "scrambles":
        run_scrambles(args.run, args.workers)
    elif args.stage == "targets":
        run_targets(args.run, args.workers, Progress(args.run / "target_progress.json"))
    else:
        run_dataset(args.run, args.real)


if __name__ == "__main__":
    main()
