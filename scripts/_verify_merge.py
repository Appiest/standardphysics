"""Dry run: does appending a capture to a combined scan leave the existing rooms untouched?"""

from __future__ import annotations

import json
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from combine_scans import overlay  # noqa: E402
from standardphysics_pipeline import parse_room_json  # noqa: E402

SCAN_ID = uuid.UUID("0b39ca6e-8174-474d-9701-f654e4a0338e")
UNION = pathlib.Path("services/api/var/scans") / str(SCAN_ID) / "artifacts" / "combined-room-json"
ADDITION = pathlib.Path("/tmp/sp-appdata/Captures/D9946491-26FD-4864-9673-C88180328109/room.json")

original_payload = json.loads(UNION.read_text())
addition_payload = json.loads(ADDITION.read_text())

before = parse_room_json(original_payload, scan_id=SCAN_ID)
merged = parse_room_json(overlay([original_payload, addition_payload]), scan_id=SCAN_ID)
addition_alone = parse_room_json(addition_payload, scan_id=SCAN_ID)

before_by_id = {node.id: node for node in before.nodes}
merged_by_id = {node.id: node for node in merged.nodes}

print(f"before: {len(before.nodes)} nodes   merged: {len(merged.nodes)} nodes")
print(f"addition alone: {len(addition_alone.nodes)} nodes")

collisions = before_by_id.keys() & {node.id for node in addition_alone.nodes}
print(f"id collisions with existing rooms: {len(collisions)}")

drifted = [
    node_id
    for node_id, node in before_by_id.items()
    if merged_by_id[node_id].transform.m != node.transform.m
]
print(f"existing nodes whose transform moved: {len(drifted)}")

print(f"capture_to_room unchanged: {before.capture_to_room == merged.capture_to_room}")

new_ids = merged_by_id.keys() - before_by_id.keys()
print(f"new nodes contributed by 'bottom left': {len(new_ids)}")

zs = sorted(merged_by_id[i].transform.m[11] for i in new_ids)
print(f"new node z range: {zs[0]:.3f} .. {zs[-1]:.3f}")
old_zs = sorted(node.transform.m[11] for node in before.nodes)
print(f"existing node z range: {old_zs[0]:.3f} .. {old_zs[-1]:.3f}")
