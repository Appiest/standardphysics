"""Propose a new, safely guarded alignment revision for the Moffett floor.

The captured graph in revision 0 contains each room in its own Z-up frame.  A
reviewed JSON file supplies one rigid transform for each capture.  This script
applies those transforms to the raw revision-0 nodes, applies the independently
measured floor-origin corrections, and copies only the display reconstruction
from the current database head.  Existing revisions and room membership are
never edited.

The default operation is a dry run.  It writes ``proposed-graph.json``,
``manifest.json`` and ``report.json`` to the proposal directory.  Passing
``--build-glb`` asks Blender to build a reviewable GLB there.  A separate
``--publish`` invocation is required to copy that GLB into a new revision and
insert the revision row; publication rechecks the database head inside a
write transaction before doing either operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from numbers import Real
from typing import Any, Mapping, Sequence

from standardphysics_contracts import Mat4, SceneGraph, graph_hash

SCAN_ID = "f143082d-f529-494b-b80d-97729234e334"  # A-102 Moffett floor.
DB_PATH = pathlib.Path("services/api/var/standardphysics.sqlite3")
SCAN_DIR = pathlib.Path("services/api/var/scans") / SCAN_ID
ROOM_KEYS = ("center", "top", "left", "bottom")

# Independently measured floor-origin offsets for the A-102 capture set.
# A caller can provide a JSON file to replace them.
DEFAULT_FLOOR_ORIGIN_CORRECTIONS_M = {
    "center": -0.1291985,
    "top": 0.1595054,
    "left": 0.0,
    "bottom": -0.1051312,
}

RIGID_TOLERANCE = 1e-5


class ProposalError(ValueError):
    """The proposal is unsafe to build or publish."""


@dataclass(frozen=True)
class DatabaseSnapshot:
    scan_id: str
    raw_revision: int
    raw_hash: str
    raw_json_sha256: str
    head_revision: int
    head_hash: str
    head_json_sha256: str
    head_model_hash: str
    raw_graph: SceneGraph
    head_graph: SceneGraph


@dataclass(frozen=True)
class Proposal:
    graph: SceneGraph
    manifest: dict[str, Any]
    report: dict[str, Any]


def _canonical_room_name(name: str) -> str:
    normalized = " ".join(str(name).strip().lower().replace("_", " ").replace("-", " ").split())
    if normalized == "bottom left":
        return "bottom"
    return normalized


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _as_matrix(value: Any, label: str) -> list[float]:
    """Read a row-major 4x4 matrix from a flat or nested JSON value."""
    if isinstance(value, Mapping):
        for key in ("matrix", "transform", "m", "transform_matrix"):
            if key in value:
                return _as_matrix(value[key], f"{label}.{key}")
        raise ProposalError(f"{label} has no matrix/transform field")

    if not isinstance(value, list):
        raise ProposalError(f"{label} must be a 4x4 matrix")
    if len(value) == 16 and all(not isinstance(item, (list, dict)) for item in value):
        values = value
    elif len(value) == 4 and all(isinstance(row, list) and len(row) == 4 for row in value):
        values = [item for row in value for item in row]
    else:
        raise ProposalError(f"{label} must be a flat 16-value or nested 4x4 matrix")

    result: list[float] = []
    for index, item in enumerate(values):
        if isinstance(item, bool) or not isinstance(item, Real):
            raise ProposalError(f"{label}[{index}] is not a number")
        number = float(item)
        if not math.isfinite(number):
            raise ProposalError(f"{label}[{index}] is not finite")
        result.append(number)
    return result


def _det3(matrix: Sequence[Sequence[float]]) -> float:
    a, b, c = matrix[0]
    d, e, f = matrix[1]
    g, h, i = matrix[2]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def validate_rigid_matrix(matrix: Sequence[float], label: str = "transform") -> list[float]:
    """Validate a proper row-major rigid transform and return a copy."""
    if len(matrix) != 16:
        raise ProposalError(f"{label} must contain 16 values")
    values = [float(value) for value in matrix]
    if not all(math.isfinite(value) for value in values):
        raise ProposalError(f"{label} contains a non-finite value")

    rotation = [values[row * 4 : row * 4 + 3] for row in range(3)]
    bottom = values[12:16]
    if any(abs(value - expected) > RIGID_TOLERANCE for value, expected in zip(bottom, (0.0, 0.0, 0.0, 1.0))):
        raise ProposalError(f"{label} has an invalid homogeneous bottom row")
    for row in rotation:
        if abs(sum(value * value for value in row) - 1.0) > RIGID_TOLERANCE:
            raise ProposalError(f"{label} rotation is not unit length")
    for left in range(3):
        for right in range(left):
            dot = sum(rotation[row][left] * rotation[row][right] for row in range(3))
            if abs(dot) > RIGID_TOLERANCE:
                raise ProposalError(f"{label} rotation is not orthogonal")
    determinant = _det3(rotation)
    if abs(determinant - 1.0) > RIGID_TOLERANCE:
        raise ProposalError(f"{label} rotation determinant is {determinant:g}, expected +1")
    return values


def _matrix_payload(data: Any) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise ProposalError("room transform file must contain an object")
    for wrapper in ("room_transforms", "transforms", "rooms"):
        candidate = data.get(wrapper)
        if isinstance(candidate, Mapping):
            return candidate
    return data


def load_room_transforms(path: pathlib.Path) -> tuple[dict[str, list[float]], str]:
    """Load all four reviewed room transforms and reject unsafe matrices."""
    raw = json.loads(path.read_text())
    payload = _matrix_payload(raw)
    found: dict[str, list[float]] = {}
    for name, value in payload.items():
        room = _canonical_room_name(name)
        if room not in ROOM_KEYS:
            continue
        if room in found:
            raise ProposalError(f"duplicate transform for room {room}")
        found[room] = validate_rigid_matrix(_as_matrix(value, f"room {name}"), f"room {name}")

    missing = [room for room in ROOM_KEYS if room not in found]
    if missing:
        raise ProposalError(f"missing room transforms: {', '.join(missing)}")
    identity = Mat4.identity().m
    if any(abs(a - b) > RIGID_TOLERANCE for a, b in zip(found["center"], identity)):
        raise ProposalError("center transform must be identity")
    return found, _sha256(path)


def _correction_vector(value: Any, label: str) -> list[float]:
    if isinstance(value, Mapping):
        if "translation" in value:
            return _correction_vector(value["translation"], f"{label}.translation")
        if "z" in value:
            values = [value.get("x", 0.0), value.get("y", 0.0), value["z"]]
        else:
            raise ProposalError(f"{label} has no z correction")
    elif isinstance(value, list):
        if len(value) != 3:
            raise ProposalError(f"{label} vector must have three values")
        values = value
    else:
        values = [0.0, 0.0, value]

    result: list[float] = []
    for index, item in enumerate(values):
        if isinstance(item, bool) or not isinstance(item, Real) or not math.isfinite(float(item)):
            raise ProposalError(f"{label}[{index}] is not a finite number")
        result.append(float(item))
    return result


def load_floor_origin_corrections(path: pathlib.Path | None) -> dict[str, list[float]]:
    if path is None:
        payload: Mapping[str, Any] = DEFAULT_FLOOR_ORIGIN_CORRECTIONS_M
    else:
        raw = json.loads(path.read_text())
        if not isinstance(raw, Mapping):
            raise ProposalError("floor-origin file must contain an object")
        payload = raw
        for wrapper in ("floor_origins", "floor_origin_corrections", "corrections"):
            if isinstance(raw.get(wrapper), Mapping):
                payload = raw[wrapper]
                break

    found: dict[str, list[float]] = {}
    for name, value in payload.items():
        room = _canonical_room_name(name)
        if room not in ROOM_KEYS:
            continue
        if room in found:
            raise ProposalError(f"duplicate floor-origin correction for room {room}")
        found[room] = _correction_vector(value, f"floor origin {name}")
    missing = [room for room in ROOM_KEYS if room not in found]
    if missing:
        raise ProposalError(f"missing floor-origin corrections: {', '.join(missing)}")
    return found


def load_room_memberships(path: pathlib.Path, graph: SceneGraph) -> dict[str, str]:
    raw = json.loads(path.read_text())
    rooms = raw.get("rooms") if isinstance(raw, Mapping) else None
    if not isinstance(rooms, list):
        raise ProposalError(f"{path} must contain a rooms list")

    known = {str(node.id) for node in graph.nodes}
    memberships: dict[str, str] = {}
    for index, room_data in enumerate(rooms):
        if not isinstance(room_data, Mapping):
            raise ProposalError(f"rooms[{index}] must be an object")
        room = _canonical_room_name(room_data.get("name", ""))
        if room not in ROOM_KEYS:
            raise ProposalError(f"rooms[{index}] has unknown room {room_data.get('name')!r}")
        node_ids = room_data.get("node_ids")
        if not isinstance(node_ids, list):
            raise ProposalError(f"room {room} has no node_ids list")
        for raw_id in node_ids:
            node_id = str(raw_id)
            if node_id not in known:
                raise ProposalError(f"room {room} names unknown node {node_id}")
            if node_id in memberships:
                raise ProposalError(f"node {node_id} belongs to more than one room")
            memberships[node_id] = room

    missing = sorted(known - memberships.keys())
    if missing:
        raise ProposalError(f"nodes missing room membership: {missing[:5]}")
    return memberships


def _matmul(left: Sequence[float], right: Sequence[float]) -> list[float]:
    return [
        sum(left[row * 4 + index] * right[index * 4 + column] for index in range(4))
        for row in range(4)
        for column in range(4)
    ]


def _translation(vector: Sequence[float]) -> list[float]:
    return [1.0, 0.0, 0.0, vector[0], 0.0, 1.0, 0.0, vector[1], 0.0, 0.0, 1.0, vector[2], 0.0, 0.0, 0.0, 1.0]


def read_database_snapshot(connection: sqlite3.Connection, scan_id: str) -> DatabaseSnapshot:
    connection.row_factory = sqlite3.Row
    raw_row = connection.execute(
        "SELECT * FROM revisions WHERE scan_id = ? AND revision = 0", (scan_id,)
    ).fetchone()
    head_row = connection.execute(
        "SELECT * FROM revisions WHERE scan_id = ? ORDER BY revision DESC LIMIT 1", (scan_id,)
    ).fetchone()
    if raw_row is None:
        raise ProposalError(f"scan {scan_id} revision 0 not found")
    if head_row is None:
        raise ProposalError(f"scan {scan_id} has no database head")

    raw_graph = SceneGraph.model_validate_json(raw_row["graph_json"])
    head_graph = SceneGraph.model_validate_json(head_row["graph_json"])
    if raw_graph.scan_id != uuid.UUID(scan_id) or head_graph.scan_id != uuid.UUID(scan_id):
        raise ProposalError("revision graph scan ID does not match requested scan")
    if raw_graph.revision != 0 or int(raw_row["revision"]) != 0:
        raise ProposalError("revision 0 row is not revision 0")
    if head_graph.revision != int(head_row["revision"]):
        raise ProposalError("database head revision and graph revision disagree")
    if graph_hash(raw_graph) != raw_row["graph_hash"]:
        raise ProposalError("revision 0 graph hash does not match its stored row")
    return DatabaseSnapshot(
        scan_id=scan_id,
        raw_revision=0,
        raw_hash=raw_row["graph_hash"],
        raw_json_sha256=hashlib.sha256(raw_row["graph_json"].encode("utf-8")).hexdigest(),
        head_revision=int(head_row["revision"]),
        head_hash=head_row["graph_hash"],
        head_json_sha256=hashlib.sha256(head_row["graph_json"].encode("utf-8")).hexdigest(),
        head_model_hash=graph_hash(head_graph),
        raw_graph=raw_graph,
        head_graph=head_graph,
    )


def build_proposed_graph(
    snapshot: DatabaseSnapshot,
    room_transforms: Mapping[str, Sequence[float]],
    floor_origin_corrections: Mapping[str, Sequence[float]],
    memberships: Mapping[str, str],
) -> SceneGraph:
    """Apply room transforms to raw nodes and retain the latest display fields."""
    raw_by_id = {str(node.id): node for node in snapshot.raw_graph.nodes}
    display_by_id = {str(node.id): node for node in snapshot.head_graph.nodes}
    if set(raw_by_id) != set(display_by_id):
        raise ProposalError("revision 0 and database head have different node IDs")
    if set(raw_by_id) != set(memberships):
        raise ProposalError("room membership does not cover revision 0 node IDs")

    nodes = []
    for node in snapshot.raw_graph.nodes:
        node_id = str(node.id)
        room = memberships[node_id]
        room_matrix = validate_rigid_matrix(room_transforms[room], f"room {room}")
        correction = _translation(floor_origin_corrections[room])
        transformed = _matmul(correction, _matmul(room_matrix, node.transform.m))
        display_node = display_by_id[node_id]
        nodes.append(
            node.model_copy(
                update={
                    "transform": Mat4(m=transformed),
                    "appearance": display_node.appearance,
                    "reconstruction": display_node.reconstruction,
                }
            )
        )

    return snapshot.raw_graph.model_copy(
        update={
            "revision": snapshot.head_revision + 1,
            "base_hash": snapshot.head_hash,
            "nodes": nodes,
        }
    )


def create_proposal(
    db_path: pathlib.Path,
    scan_id: str,
    room_transforms_path: pathlib.Path,
    rooms_path: pathlib.Path,
    floor_origin_path: pathlib.Path | None,
    output_dir: pathlib.Path,
    build_glb: bool = False,
) -> Proposal:
    with sqlite3.connect(db_path) as connection:
        snapshot = read_database_snapshot(connection, scan_id)

    room_transforms, transform_digest = load_room_transforms(room_transforms_path)
    floor_corrections = load_floor_origin_corrections(floor_origin_path)
    memberships = load_room_memberships(rooms_path, snapshot.raw_graph)
    proposed = build_proposed_graph(snapshot, room_transforms, floor_corrections, memberships)
    proposed_hash = graph_hash(proposed)
    output_dir.mkdir(parents=True, exist_ok=True)

    graph_file = output_dir / "proposed-graph.json"
    _write_json(graph_file, proposed.model_dump(mode="json"))

    glb_file: pathlib.Path | None = None
    if build_glb:
        from standardphysics_pipeline.blender import export_glb

        glb_file = output_dir / "proposed-scene.glb"
        export_glb(proposed, glb_file)

    room_counts = {room: sum(value == room for value in memberships.values()) for room in ROOM_KEYS}
    display_nodes = sum(node.reconstruction is not None for node in proposed.nodes)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "status": "proposed",
        "scan_id": scan_id,
        "source_revision": 0,
        "display_revision": snapshot.head_revision,
        "proposed_revision": proposed.revision,
        "database_head": {"revision": snapshot.head_revision, "graph_hash": snapshot.head_hash},
        "source_revision_hash": snapshot.raw_hash,
        "source_revision_json_sha256": snapshot.raw_json_sha256,
        "database_head_json_sha256": snapshot.head_json_sha256,
        "database_head_model_hash": snapshot.head_model_hash,
        "proposed_graph_hash": proposed_hash,
        "room_transforms": {room: room_transforms[room] for room in ROOM_KEYS},
        "room_transforms_path": str(room_transforms_path.resolve()),
        "room_transforms_sha256": transform_digest,
        "floor_origin_corrections_m": {room: floor_corrections[room] for room in ROOM_KEYS},
        "rooms_path": str(rooms_path.resolve()),
        "rooms_sha256": _sha256(rooms_path),
        "room_node_counts": room_counts,
        "node_count": len(proposed.nodes),
        "display_reconstruction_count": display_nodes,
        "files": {
            "graph": graph_file.name,
            "glb": glb_file.name if glb_file else None,
            "manifest": "manifest.json",
            "report": "report.json",
        },
    }
    report = {
        "status": "dry_run",
        "checks": {
            "database_head_snapshot": "passed",
            "revision_0_source": "passed",
            "room_transforms_rigid": "passed",
            "center_transform_identity": "passed",
            "room_membership_complete": "passed",
            "display_parts_preserved_by_node_id": "passed",
            "existing_revisions_untouched": "publication_guarded",
            "stored_head_hash_consistency": (
                "passed" if snapshot.head_hash == snapshot.head_model_hash else "historical_mismatch_recorded"
            ),
        },
        "scan_id": scan_id,
        "source_revision": 0,
        "display_revision": snapshot.head_revision,
        "proposed_revision": proposed.revision,
        "node_count": len(proposed.nodes),
        "room_node_counts": room_counts,
        "display_reconstruction_count": display_nodes,
        "blender_built": glb_file is not None,
        "publication": "not performed",
    }
    _write_json(output_dir / "manifest.json", manifest)
    _write_json(output_dir / "report.json", report)
    return Proposal(graph=proposed, manifest=manifest, report=report)


def _verify_proposal_inputs(manifest: Mapping[str, Any], graph: SceneGraph) -> None:
    if manifest.get("status") != "proposed":
        raise ProposalError("manifest is not a dry-run proposal")
    if graph_hash(graph) != manifest.get("proposed_graph_hash"):
        raise ProposalError("proposed graph hash does not match manifest")
    transform_path = pathlib.Path(manifest["room_transforms_path"])
    rooms_path = pathlib.Path(manifest["rooms_path"])
    if not transform_path.exists() or _sha256(transform_path) != manifest["room_transforms_sha256"]:
        raise ProposalError("room transform input changed since dry run")
    if not rooms_path.exists() or _sha256(rooms_path) != manifest["rooms_sha256"]:
        raise ProposalError("room membership input changed since dry run")


def _refuse_if_the_database_moved(snapshot, manifest: dict, graph: SceneGraph, expected_head: dict) -> None:
    """Every way the database can have changed since the dry run read it.

    A publication is only safe if what it was planned against is still there, so
    these are checked under the write lock and together, which is also why they
    belong in one place rather than strung through the publishing itself.
    """
    if snapshot.head_revision != expected_head["revision"] or snapshot.head_hash != expected_head["graph_hash"]:
        raise ProposalError("database head changed since dry run; refusing publication")
    if snapshot.raw_hash != manifest["source_revision_hash"]:
        raise ProposalError("revision 0 changed since dry run; refusing publication")
    if snapshot.raw_json_sha256 != manifest["source_revision_json_sha256"]:
        raise ProposalError("revision 0 JSON changed since dry run; refusing publication")
    if snapshot.head_json_sha256 != manifest["database_head_json_sha256"]:
        raise ProposalError("database head graph changed since dry run; refusing publication")
    if graph.revision != snapshot.head_revision + 1 or graph.base_hash != snapshot.head_hash:
        raise ProposalError("proposal is not the next revision of the current database head")


def publish_proposal(db_path: pathlib.Path, output_dir: pathlib.Path) -> pathlib.Path:
    """Publish a reviewed dry-run proposal as one new revision.

    The current database head is checked after taking SQLite's write lock.  No
    existing revision row is updated, and the GLB is copied only after all
    proposal checks have passed.
    """
    manifest_path = output_dir / "manifest.json"
    graph_path = output_dir / "proposed-graph.json"
    if not manifest_path.exists() or not graph_path.exists():
        raise ProposalError(f"{output_dir} is not a complete dry-run proposal")
    manifest = json.loads(manifest_path.read_text())
    graph = SceneGraph.model_validate_json(graph_path.read_text())
    _verify_proposal_inputs(manifest, graph)

    files = manifest.get("files", {})
    glb_name = files.get("glb")
    if not glb_name:
        raise ProposalError("publish requires a Blender GLB from the dry run")
    source_glb = output_dir / glb_name
    if not source_glb.is_file():
        raise ProposalError(f"proposal GLB is missing: {source_glb}")

    scan_id = str(manifest["scan_id"])
    expected_head = manifest["database_head"]
    destination: pathlib.Path | None = None
    created_destination = False
    connection = sqlite3.connect(db_path, isolation_level=None, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        snapshot = read_database_snapshot(connection, scan_id)
        _refuse_if_the_database_moved(snapshot, manifest, graph, expected_head)
        target_row = connection.execute(
            "SELECT 1 FROM revisions WHERE scan_id = ? AND revision = ?",
            (scan_id, graph.revision),
        ).fetchone()
        if target_row is not None:
            raise ProposalError(f"revision {graph.revision} already exists")

        destination = (SCAN_DIR.parent / scan_id / "revisions" / str(graph.revision) / "scene.glb").resolve()
        if destination.exists():
            raise ProposalError(f"publication asset already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=False)
        created_destination = True
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        shutil.copyfile(source_glb, temporary)
        temporary.replace(destination)

        from standardphysics_api.repository import save_revision

        save_revision(
            connection,
            graph,
            source="owner",
            base_revision=snapshot.head_revision,
            glb_path=str(destination),
        )
        connection.commit()
        return destination
    except BaseException:
        connection.rollback()
        if created_destination and destination is not None:
            shutil.rmtree(destination.parent, ignore_errors=True)
        raise
    finally:
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="write a proposal without DB or asset publication (default)")
    mode.add_argument("--publish", action="store_true", help="publish an existing reviewed proposal directory")
    parser.add_argument("--scan-id", default=SCAN_ID)
    parser.add_argument("--db", type=pathlib.Path, default=DB_PATH)
    parser.add_argument("--rooms", type=pathlib.Path, default=SCAN_DIR / "rooms.json")
    parser.add_argument("--transforms", type=pathlib.Path, help="reviewed room-transform JSON for a dry run")
    parser.add_argument("--floor-origins", type=pathlib.Path, help="optional independent floor-origin correction JSON")
    parser.add_argument("--output", type=pathlib.Path, default=pathlib.Path("runs/moffett/a102-revision-proposal"))
    parser.add_argument("--build-glb", action="store_true", help="build a review GLB inside the proposal directory")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.publish:
        destination = publish_proposal(args.db, args.output)
        print(f"published revision GLB: {destination}")
        return 0
    if args.transforms is None:
        _parser().error("--transforms is required for a dry run")
    proposal = create_proposal(
        db_path=args.db,
        scan_id=args.scan_id,
        room_transforms_path=args.transforms,
        rooms_path=args.rooms,
        floor_origin_path=args.floor_origins,
        output_dir=args.output,
        build_glb=args.build_glb,
    )
    print(json.dumps({
        "status": proposal.report["status"],
        "scan_id": args.scan_id,
        "proposed_revision": proposal.graph.revision,
        "graph_hash": proposal.manifest["proposed_graph_hash"],
        "output": str(args.output.resolve()),
        "publication": "not performed",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
