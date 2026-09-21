from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from standardphysics_contracts import (
    DisplayAppearance,
    DisplayPart,
    DisplayReconstruction,
    Mat4,
    SceneGraph,
    SceneNode,
    Vec3,
    graph_hash,
)

from scripts import align_moffett_floor as align

SCAN_ID = uuid.UUID("00000000-0000-0000-0000-000000000102")
IDENTITY = Mat4.identity().m


def _translation(x: float, y: float, z: float) -> list[float]:
    return Mat4.translation(x, y, z).m


def _node(index: int, transform: list[float], *, display: bool = False) -> SceneNode:
    node = SceneNode(
        id=uuid.uuid5(SCAN_ID, f"node-{index}"),
        kind="object",
        label=f"node {index}",
        raw_category="table",
        dimensions=Vec3(x=1.0, y=1.0, z=1.0),
        transform=Mat4(m=transform),
    )
    if display:
        return node.model_copy(
            update={
                "appearance": DisplayAppearance(base_color="#112233", material="wood"),
                "reconstruction": DisplayReconstruction(
                    summary=f"display for node {index}",
                    confidence=0.9,
                    evidence_frame_ids=[f"frame-{index}"],
                    parts=[
                        DisplayPart(
                            name="top",
                            primitive="box",
                            center=[0.0, 0.0, 0.0],
                            size=[0.5, 0.5, 0.5],
                            base_color="#445566",
                            material="wood",
                        )
                    ],
                ),
            }
        )
    return node


def _graphs() -> tuple[SceneGraph, SceneGraph]:
    raw_nodes = [
        _node(0, _translation(1.0, 2.0, 3.0)),
        _node(1, _translation(4.0, 5.0, 6.0)),
        _node(2, _translation(7.0, 8.0, 9.0)),
        _node(3, _translation(10.0, 11.0, 12.0)),
    ]
    raw = SceneGraph(scan_id=SCAN_ID, revision=0, nodes=raw_nodes)
    # Reverse the order to ensure display fields are matched by node ID rather
    # than by position in the graph.
    head_nodes = [
        raw_nodes[3].model_copy(update={"appearance": None, "reconstruction": None}),
        _node(2, raw_nodes[2].transform.m, display=True),
        _node(1, raw_nodes[1].transform.m, display=True),
        _node(0, raw_nodes[0].transform.m, display=True),
    ]
    head = SceneGraph(scan_id=SCAN_ID, revision=2, base_hash=graph_hash(raw), nodes=head_nodes)
    return raw, head


def _create_database(path: Path) -> tuple[SceneGraph, SceneGraph]:
    raw, head = _graphs()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE revisions ("
            "scan_id TEXT NOT NULL, revision INTEGER NOT NULL, graph_hash TEXT NOT NULL, "
            "graph_json TEXT NOT NULL, source TEXT NOT NULL, base_revision INTEGER, "
            "glb_path TEXT, created_at TEXT NOT NULL, PRIMARY KEY (scan_id, revision))"
        )
        for graph in (raw, head):
            connection.execute(
                "INSERT INTO revisions "
                "(scan_id, revision, graph_hash, graph_json, source, base_revision, glb_path, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(SCAN_ID),
                    graph.revision,
                    graph_hash(graph),
                    graph.model_dump_json(),
                    "test",
                    graph.revision - 1 if graph.revision else None,
                    None,
                    "2026-01-01T00:00:00+00:00",
                ),
            )
    return raw, head


def _memberships(raw: SceneGraph) -> dict[str, str]:
    rooms = ("center", "top", "left", "bottom")
    return {str(node.id): room for node, room in zip(raw.nodes, rooms)}


def _write_inputs(tmp_path: Path, raw: SceneGraph, *, transforms: dict | None = None, rooms: dict | None = None):
    transform_path = tmp_path / "transforms.json"
    transform_path.write_text(
        json.dumps(
            transforms
            or {
                "room_transforms": {room: IDENTITY for room in ("center", "top", "left", "bottom left")}
            }
        )
    )
    room_path = tmp_path / "rooms.json"
    room_path.write_text(
        json.dumps(
            rooms
            or {
                "rooms": [
                    {"name": room, "node_ids": [str(node.id)]}
                    for node, room in zip(raw.nodes, ("center", "top", "left", "bottom left"))
                ]
            }
        )
    )
    return transform_path, room_path


def _snapshot_rows(path: Path) -> list[tuple]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT revision, graph_hash, graph_json, glb_path FROM revisions "
            "WHERE scan_id = ? ORDER BY revision",
            (str(SCAN_ID),),
        ).fetchall()


def _publishable_proposal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path, list[tuple]]:
    db_path = tmp_path / "database.sqlite3"
    raw, _ = _create_database(db_path)
    transform_path, rooms_path = _write_inputs(tmp_path, raw)
    output_dir = tmp_path / "proposal"
    align.create_proposal(
        db_path=db_path,
        scan_id=str(SCAN_ID),
        room_transforms_path=transform_path,
        rooms_path=rooms_path,
        floor_origin_path=None,
        output_dir=output_dir,
    )
    (output_dir / "proposed-scene.glb").write_bytes(b"new glb")
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["glb"] = "proposed-scene.glb"
    manifest_path.write_text(json.dumps(manifest))

    asset_root = tmp_path / "scans"
    monkeypatch.setattr(align, "SCAN_DIR", asset_root / str(SCAN_ID))
    return db_path, output_dir, asset_root, _snapshot_rows(db_path)


def test_missing_transform_and_invalid_rigid_matrix_are_rejected(tmp_path: Path) -> None:
    missing = {"room_transforms": {room: IDENTITY for room in ("center", "top", "left")}}
    missing_path = tmp_path / "missing.json"
    missing_path.write_text(json.dumps(missing))
    with pytest.raises(align.ProposalError, match="missing room transforms: bottom"):
        align.load_room_transforms(missing_path)

    reflection = IDENTITY.copy()
    reflection[0] = -1.0
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text(
        json.dumps(
            {
                "room_transforms": {
                    "center": IDENTITY,
                    "top": reflection,
                    "left": IDENTITY,
                    "bottom": IDENTITY,
                }
            }
        )
    )
    with pytest.raises(align.ProposalError, match="determinant"):
        align.load_room_transforms(invalid_path)


def test_center_transform_must_be_identity(tmp_path: Path) -> None:
    center = IDENTITY.copy()
    center[3] = 0.01
    path = tmp_path / "transforms.json"
    path.write_text(
        json.dumps(
            {
                "room_transforms": {
                    "center": center,
                    "top": IDENTITY,
                    "left": IDENTITY,
                    "bottom": IDENTITY,
                }
            }
        )
    )
    with pytest.raises(align.ProposalError, match="center transform must be identity"):
        align.load_room_transforms(path)


def test_room_transform_and_floor_correction_are_composed_in_order(tmp_path: Path) -> None:
    raw, head = _graphs()
    snapshot = align.DatabaseSnapshot(
        scan_id=str(SCAN_ID),
        raw_revision=0,
        raw_hash=graph_hash(raw),
        raw_json_sha256="raw",
        head_revision=head.revision,
        head_hash=graph_hash(head),
        head_json_sha256="head",
        head_model_hash=graph_hash(head),
        raw_graph=raw,
        head_graph=head,
    )
    rotation = [0.0, -1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    transforms = {room: IDENTITY for room in ("center", "top", "left", "bottom")}
    transforms["top"] = rotation
    corrections = {room: [0.0, 0.0, 0.0] for room in ("center", "top", "left", "bottom")}
    corrections["top"] = [10.0, 20.0, 30.0]
    memberships = _memberships(raw)

    proposed = align.build_proposed_graph(snapshot, transforms, corrections, memberships)
    target = next(node for node in proposed.nodes if node.id == raw.nodes[1].id)
    assert target.transform.m == pytest.approx(
        [0.0, -1.0, 0.0, 5.0, 1.0, 0.0, 0.0, 24.0, 0.0, 0.0, 1.0, 36.0, 0.0, 0.0, 0.0, 1.0]
    )


def test_display_fields_are_preserved_by_node_id(tmp_path: Path) -> None:
    raw, head = _graphs()
    snapshot = align.DatabaseSnapshot(
        scan_id=str(SCAN_ID),
        raw_revision=0,
        raw_hash=graph_hash(raw),
        raw_json_sha256="raw",
        head_revision=head.revision,
        head_hash=graph_hash(head),
        head_json_sha256="head",
        head_model_hash=graph_hash(head),
        raw_graph=raw,
        head_graph=head,
    )
    proposed = align.build_proposed_graph(
        snapshot,
        {room: IDENTITY for room in ("center", "top", "left", "bottom")},
        {room: [0.0, 0.0, 0.0] for room in ("center", "top", "left", "bottom")},
        _memberships(raw),
    )
    head_by_id = {node.id: node for node in head.nodes}
    for node in proposed.nodes:
        assert node.appearance == head_by_id[node.id].appearance
        assert node.reconstruction == head_by_id[node.id].reconstruction


@pytest.mark.parametrize(
    ("rooms", "message"),
    [
        (
            {"rooms": [{"name": "center", "node_ids": [str(uuid.uuid5(SCAN_ID, "node-0"))]}]},
            "missing room membership",
        ),
        (
            {
                "rooms": [
                    {"name": "center", "node_ids": [str(uuid.uuid5(SCAN_ID, "node-0"))]},
                    {"name": "top", "node_ids": [str(uuid.uuid5(SCAN_ID, "node-0"))]},
                    {"name": "left", "node_ids": [str(uuid.uuid5(SCAN_ID, "node-2"))]},
                    {"name": "bottom", "node_ids": [str(uuid.uuid5(SCAN_ID, "node-3"))]},
                ]
            },
            "more than one room",
        ),
    ],
)
def test_room_memberships_must_be_complete_and_unique(
    tmp_path: Path, rooms: dict, message: str
) -> None:
    raw, _ = _graphs()
    rooms_path = tmp_path / "rooms.json"
    rooms_path.write_text(json.dumps(rooms))
    with pytest.raises(align.ProposalError, match=message):
        align.load_room_memberships(rooms_path, raw)


def test_dry_run_writes_review_files_without_mutating_database(tmp_path: Path) -> None:
    db_path = tmp_path / "database.sqlite3"
    raw, _ = _create_database(db_path)
    transform_path, rooms_path = _write_inputs(tmp_path, raw)
    before = _snapshot_rows(db_path)

    proposal = align.create_proposal(
        db_path=db_path,
        scan_id=str(SCAN_ID),
        room_transforms_path=transform_path,
        rooms_path=rooms_path,
        floor_origin_path=None,
        output_dir=tmp_path / "proposal",
    )

    output_dir = tmp_path / "proposal"
    assert proposal.graph.revision == 3
    assert {path.name for path in output_dir.iterdir()} == {
        "proposed-graph.json",
        "manifest.json",
        "report.json",
    }
    manifest = json.loads((output_dir / "manifest.json").read_text())
    report = json.loads((output_dir / "report.json").read_text())
    assert manifest["proposed_revision"] == 3
    assert manifest["files"]["glb"] is None
    assert report["publication"] == "not performed"
    assert _snapshot_rows(db_path) == before


def test_publish_refuses_existing_scene_without_modifying_asset_or_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, output_dir, asset_root, before = _publishable_proposal(tmp_path, monkeypatch)
    destination_dir = asset_root / str(SCAN_ID) / "revisions" / "3"
    destination_dir.mkdir(parents=True)
    destination = destination_dir / "scene.glb"
    destination.write_bytes(b"existing glb")

    with pytest.raises(align.ProposalError, match="publication asset already exists"):
        align.publish_proposal(db_path, output_dir)

    assert destination.read_bytes() == b"existing glb"
    assert _snapshot_rows(db_path) == before


def test_publish_refuses_existing_revision_directory_without_deleting_artifact_or_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, output_dir, asset_root, before = _publishable_proposal(tmp_path, monkeypatch)
    destination_dir = asset_root / str(SCAN_ID) / "revisions" / "3"
    destination_dir.mkdir(parents=True)
    sentinel = destination_dir / "other-artifact.bin"
    sentinel.write_bytes(b"keep this artifact")

    with pytest.raises(FileExistsError):
        align.publish_proposal(db_path, output_dir)

    assert sentinel.read_bytes() == b"keep this artifact"
    assert not (destination_dir / "scene.glb").exists()
    assert _snapshot_rows(db_path) == before


def test_publish_refuses_a_changed_database_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "database.sqlite3"
    raw, head = _create_database(db_path)
    transform_path, rooms_path = _write_inputs(tmp_path, raw)
    output_dir = tmp_path / "proposal"
    align.create_proposal(
        db_path=db_path,
        scan_id=str(SCAN_ID),
        room_transforms_path=transform_path,
        rooms_path=rooms_path,
        floor_origin_path=None,
        output_dir=output_dir,
    )
    glb_name = "proposed-scene.glb"
    (output_dir / glb_name).write_bytes(b"glb")
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["glb"] = glb_name
    manifest_path.write_text(json.dumps(manifest))

    changed = head.model_copy(
        update={"revision": 3, "base_hash": graph_hash(head), "nodes": [_node(0, _translation(99.0, 0.0, 0.0), display=True), *head.nodes[1:]]}
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO revisions "
            "(scan_id, revision, graph_hash, graph_json, source, base_revision, glb_path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(SCAN_ID), 3, graph_hash(changed), changed.model_dump_json(), "test", 2, None, "2026-01-02T00:00:00+00:00"),
        )

    asset_root = tmp_path / "scans"
    monkeypatch.setattr(align, "SCAN_DIR", asset_root / str(SCAN_ID))
    with pytest.raises(align.ProposalError, match="database head changed"):
        align.publish_proposal(db_path, output_dir)
    assert not (asset_root / str(SCAN_ID) / "revisions" / "3" / "scene.glb").exists()
