"""Turn the trained model's answers on web app rooms into revision requests for those scans.

For each web app room in the evaluation file, the answer with the highest reward (the product retries
too, so best of the samples is what a user would see) is snapped exactly as the reward snaps it, and the
snapped layout is written as a `SaveLayoutRequest` body: base revision 0 and the moves from the scan's
layout to the snapped one. Nothing is sent; the bodies are posted from the owner's signed-in session.

    python scripts/finetune/webapp_revisions.py --run runs/finetune/harness --eval rl --out revisions.json
"""

from __future__ import annotations

import argparse
import json
import pathlib

from multiroom_data import checker_for
from standardphysics_agents.snap import snap
from standardphysics_agents.training import edits_between, parse_edits
from standardphysics_agents.training.edits import node_moves
from standardphysics_agents.training.windows import Window
from standardphysics_contracts import NodeMove, SceneGraph, Vec3

WEBAPP_PREFIX = "webapp-"


def _rows(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def best_answers(records: list[dict]) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for record in records:
        if record["variant"].startswith(WEBAPP_PREFIX):
            kept = best.get(record["variant"])
            if kept is None or record["reward"] > kept["reward"]:
                best[record["variant"]] = record
    return best


def revision_body(record: dict, window: Window, graph: SceneGraph) -> dict:
    checker = checker_for(window)
    edits = parse_edits(record["completion"])
    snapped = snap(graph, node_moves(edits), checker.directive_rejection(graph)).graph
    moves = [NodeMove(node_id=move.node_id, delta_translation=Vec3(x=move.dx, y=move.dy, z=0.0),
                      delta_rotation_z_degrees=move.rotation_degrees)
             for move in edits_between(graph, snapped).moves]
    return {"scan_id": window.scan_id, "variant": record["variant"], "reward": record["reward"],
            "reason": record.get("reason", ""), "shortfall_recovered": record.get("shortfall_recovered"),
            "body": {"base_revision": 0, "moves": [move.model_dump(mode="json") for move in moves]}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=pathlib.Path, required=True)
    parser.add_argument("--eval", default="rl")
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    windows = {row["window_id"]: Window.from_dict(row) for row in _rows(args.run / "windows.jsonl")}
    variants = {row["variant_id"]: row for row in _rows(args.run / "variants.jsonl") if row.get("variant_id")}
    records = _rows(args.run / "qwen3p8-27b" / "eval" / f"{args.eval}.jsonl")
    bodies = []
    for variant_id, record in sorted(best_answers(records).items()):
        variant = variants[variant_id]
        if parse_edits(record["completion"]) is None:
            continue
        bodies.append(revision_body(record, windows[variant["window_id"]], SceneGraph.model_validate(variant["graph"])))
    args.out.write_text(json.dumps(bodies, indent=2) + "\n")
    print(json.dumps([{k: v for k, v in body.items() if k != "body"} | {"moves": len(body["body"]["moves"])}
                      for body in bodies], indent=2))


if __name__ == "__main__":
    main()
