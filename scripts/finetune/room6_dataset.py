"""Build the room 6 rearrangement dataset: scrambled variants, search targets, prompts.

Reads the local database read-only through the API's repository functions and
writes JSONL under runs/finetune/room6/data/:

    variants.jsonl   every variant: name, split, graph, fixable checks, search target
    sft.jsonl        {"messages": [...system, user, assistant]} for trainable variants the search fixed
    rl.jsonl         {"messages": [...], "variant": name} for every training variant
    heldout.jsonl    {"messages": [...], "variant": name} for the held-out variants, never trained on
    context.json     scanned room and scenario, for rebuilding the checker elsewhere

    python scripts/finetune/room6_dataset.py --count 60 --heldout 10
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import sqlite3
import uuid

from standardphysics_agents.training import (
    TrainingChecker,
    edits_between,
    edits_json,
    prompt_messages,
    scramble,
    search_target,
    searched_layout,
    trusted_geometry,
)

from standardphysics_api import repository

ROOM6_SCAN_ID = uuid.UUID("cdb7ced5-b67f-4f94-8639-0257a1dd8e9a")
ROOM6_REVISION = 4
DEFAULT_DB = pathlib.Path(__file__).resolve().parents[2] / "services/api/var/standardphysics.sqlite3"
DEFAULT_OUT = pathlib.Path(__file__).resolve().parents[2] / "runs/finetune/room6/data"


def read_room(database: pathlib.Path, scan_id: uuid.UUID, revision: int):
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        graph = repository.graph_of(repository.get_revision(connection, scan_id, revision))
        scenario = repository.get_scenario(connection, scan_id)
    finally:
        connection.close()
    return trusted_geometry(graph), scenario


def _write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _record(variant, split: str, checker: TrainingChecker) -> dict:
    target = search_target(variant.graph, checker)
    return {
        "variant": variant.name,
        "split": split,
        "fixable": list(variant.fixable),
        "graph": variant.graph.model_dump(mode="json"),
        "messages": prompt_messages(variant.graph, checker),
        "target": None if target is None else target.completion,
        "target_verdict": None if target is None else target.verdict.as_dict(),
    }


def build(database: pathlib.Path, out: pathlib.Path, count: int, heldout: int, seed: int) -> dict:
    scanned, scenario = read_room(database, ROOM6_SCAN_ID, ROOM6_REVISION)
    checker = TrainingChecker(scenario)
    starts = [scanned, searched_layout(scanned, checker)]
    variants = scramble(scanned, checker, count, seed=seed, starts=starts)
    order = list(range(len(variants)))
    random.Random(seed).shuffle(order)
    held = set(order[:heldout])
    records = [_record(v, "heldout" if i in held else "train", checker) for i, v in enumerate(variants)]

    out.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out / "variants.jsonl", records)
    train = [r for r in records if r["split"] == "train"]
    sft = [{"messages": [*r["messages"], {"role": "assistant", "content": r["target"]}], "variant": r["variant"]}
           for r in train if r["target"]]
    _write_jsonl(out / "sft.jsonl", sft)
    _write_jsonl(out / "rl.jsonl", [{"messages": r["messages"], "variant": r["variant"]} for r in train])
    _write_jsonl(out / "heldout.jsonl", [{"messages": r["messages"], "variant": r["variant"]}
                                         for r in records if r["split"] == "heldout"])
    (out / "context.json").write_text(json.dumps({
        "scan_id": str(ROOM6_SCAN_ID), "revision": ROOM6_REVISION,
        "scanned": scanned.model_dump(mode="json"), "scenario": scenario.model_dump(mode="json"),
        "searched_scanned_room": edits_json(edits_between(scanned, starts[1])),
    }))
    return {"variants": len(records), "train": len(train), "heldout": len(records) - len(train),
            "sft": len(sft), "train_without_search_fix": len(train) - len(sft)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=pathlib.Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    parser.add_argument("--count", type=int, default=60)
    parser.add_argument("--heldout", type=int, default=10)
    parser.add_argument("--seed", type=int, default=6)
    args = parser.parse_args()
    print(json.dumps(build(args.database, args.out, args.count, args.heldout, args.seed)))


if __name__ == "__main__":
    main()
