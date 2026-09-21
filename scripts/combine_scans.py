"""Combine several room captures into one scan, and record which nodes are in which room.

Independent phone captures do not necessarily share a horizontal coordinate
frame. This creates an initial overlay with normalized floor heights. The
workspace can then align the rooms using their per-room node memberships,
recorded next to the scan as `rooms.json`.

    .venv/bin/python scripts/combine_scans.py --name "Whole floor" --owner you@example.com \
        CAPTURE_DIR [CAPTURE_DIR ...]

The room name is read from each capture's `capture.json`; directory order is the
fallback when a capture carries no name.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import pathlib
import uuid

from standardphysics_contracts import Artifact, CreateScanRequest
from standardphysics_pipeline import blender, parse_room_json
from standardphysics_pipeline.ingest import capture_to_room_from_payload

from standardphysics_api import accounts
from standardphysics_api.db import Database
from standardphysics_api.repository import (
    insert_artifact,
    insert_scan,
    save_revision,
    set_state,
)
from standardphysics_api.settings import Settings
from standardphysics_api.store import ArtifactStore

SURFACE_KEYS = ("walls", "doors", "windows", "openings", "floors", "objects", "sections")
SCALAR_KEYS = ("version", "story", "coreModel", "referenceOriginTransform")


MAX_CLOSED_WALL_LENGTH = 18.0
MAX_OPEN_WALL_LENGTH = 12.0


def _is_spurious_wall(wall: dict) -> bool:
    length = wall.get("dimensions", [0])[0]
    edges = wall.get("completedEdges") or []
    return length > MAX_CLOSED_WALL_LENGTH or (length > MAX_OPEN_WALL_LENGTH and len(edges) < 2)


def filter_spurious_elements(payload: dict) -> dict:
    walls = payload.get("walls") or []
    spurious_ids = {w["identifier"] for w in walls if _is_spurious_wall(w)}
    if not spurious_ids:
        return payload
    filtered = dict(payload)
    filtered["walls"] = [w for w in walls if w["identifier"] not in spurious_ids]
    for key in ("doors", "windows", "openings"):
        if key in filtered and filtered[key]:
            filtered[key] = [p for p in filtered[key] if p.get("parentIdentifier") not in spurious_ids]
    return filtered


def load_payload(directory: pathlib.Path) -> dict:
    room_json = directory / "room.json"
    if not room_json.exists():
        raise SystemExit(f"{directory}: no room.json")
    return filter_spurious_elements(json.loads(room_json.read_text()))


def room_name(directory: pathlib.Path, index: int) -> str:
    capture = directory / "capture.json"
    if capture.exists():
        try:
            name = json.loads(capture.read_text()).get("name")
            if name:
                return name
        except (json.JSONDecodeError, OSError):
            pass
    return f"room {index + 1}"


def _floor_y(payload: dict) -> float:
    """Read the first floor's ARKit Y coordinate without changing the payload."""
    return -capture_to_room_from_payload(payload).m[11]


def _shift_transform_y(transform: object, delta: float) -> object:
    """Shift ARKit translation Y while retaining the transform's shape."""
    if isinstance(transform, list) and len(transform) == 16:
        shifted = list(transform)
        shifted[13] = float(shifted[13]) + delta
        return shifted
    if (
        isinstance(transform, list)
        and len(transform) == 4
        and all(isinstance(column, list) and len(column) == 4 for column in transform)
    ):
        shifted = [list(column) for column in transform]
        shifted[3][1] = float(shifted[3][1]) + delta
        return shifted
    return transform


def _normalize_floor(payload: dict, reference_floor_y: float) -> dict:
    """Copy a capture and move every element to the reference floor height."""
    normalized = copy.deepcopy(payload)
    delta = reference_floor_y - _floor_y(payload)
    if delta == 0.0:
        return normalized

    for key in SURFACE_KEYS:
        for element in normalized.get(key) or []:
            if isinstance(element, dict) and "transform" in element:
                element["transform"] = _shift_transform_y(element["transform"], delta)
    return normalized


def overlay(payloads: list[dict]) -> dict:
    reference_floor_y = _floor_y(payloads[0])
    normalized = [_normalize_floor(payload, reference_floor_y) for payload in payloads]
    combined = copy.deepcopy(normalized[0])
    for key in SURFACE_KEYS:
        combined[key] = [element for payload in normalized for element in (payload.get(key) or [])]
    return combined


def room_groups(directories: list[pathlib.Path]) -> list[dict]:
    return [
        {"name": room_name(directory, index), "node_ids": [str(node.id) for node in parse_room_json(load_payload(directory)).nodes]}
        for index, directory in enumerate(directories)
    ]


def store_room_json(connection, store: ArtifactStore, scan_id: uuid.UUID, payload: dict) -> None:
    artifact_id = "combined-room-json"
    data = json.dumps(payload).encode()
    path = store.artifact_path(scan_id, artifact_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    artifact = Artifact(
        id=artifact_id,
        kind="room_json",
        sha256=hashlib.sha256(data).hexdigest(),
        bytes=len(data),
    )
    insert_artifact(connection, scan_id, artifact)


def combine_into_scan(
    directories: list[pathlib.Path],
    name: str,
    owner_email: str,
    settings: Settings | None = None,
) -> uuid.UUID:
    settings = settings or Settings.from_environment()
    database = Database(settings.database_path)
    store = ArtifactStore(settings.data_dir, settings.max_artifact_bytes)

    payload = overlay([load_payload(directory) for directory in directories])
    scan_id = uuid.uuid4()
    graph = parse_room_json(payload, scan_id=scan_id)

    revision_dir = store.scan_dir(scan_id) / "revisions" / "0"
    glb_path = blender.export_glb(graph, revision_dir / "scene.glb")

    with database.transaction() as connection:
        owner = connection.execute(
            "SELECT id FROM owners WHERE email = ?", (accounts.normalize_email(owner_email),)
        ).fetchone()
        if owner is None:
            raise SystemExit(f"no account for {owner_email}; sign up or pass --owner")
        request = CreateScanRequest(name=name, device_model="combined rooms", duration_seconds=0.0)
        insert_scan(connection, request, uuid.UUID(owner["id"]), scan_id=scan_id, state="ready")
        store_room_json(connection, store, scan_id, payload)
        save_revision(connection, graph, source="ingest", glb_path=str(glb_path))
        set_state(connection, scan_id, "ready")

    (store.scan_dir(scan_id) / "rooms.json").write_text(json.dumps({"rooms": room_groups(directories)}))
    return scan_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+", type=pathlib.Path)
    parser.add_argument("--name", default="Combined scan")
    parser.add_argument("--owner", default="demo@standardphysics.app")
    args = parser.parse_args()

    scan_id = combine_into_scan(args.directories, args.name, args.owner)
    print(f"combined {len(args.directories)} rooms into {scan_id}")
    print(f"open http://localhost:3000/scans/{scan_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
