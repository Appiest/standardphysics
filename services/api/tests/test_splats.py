from __future__ import annotations

import json
import uuid

import pytest
from standardphysics_contracts import SceneGraph, graph_hash

from standardphysics_api import repository as repo

IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def _scan(client) -> tuple[str, SceneGraph]:
    scan_id = client.get("/api/scans").json()["scans"][0]["id"]
    graph = SceneGraph.model_validate(client.get(f"/api/scans/{scan_id}/scene").json())
    return scan_id, graph


def _write_manifest(client, scan_id: str, graph: SceneGraph, assets: list[dict]) -> None:
    directory = client.app.state.store.scan_dir(uuid.UUID(scan_id)) / "revisions" / str(graph.revision) / "splats"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps({
        "revision": graph.revision,
        "graph_hash": graph_hash(graph),
        "assets": assets,
    }))


def test_splats_require_a_session_and_serve_revision_pinned_assets(make_client):
    with make_client(seed=True) as client:
        scan_id, graph = _scan(client)
        _write_manifest(client, scan_id, graph, [{"filename": "room.ply", "transform": IDENTITY}])
        directory = client.app.state.store.scan_dir(uuid.UUID(scan_id)) / "revisions" / "0" / "splats"
        (directory / "room.ply").write_bytes(b"ply fixture")

        manifest = client.get(f"/api/scans/{scan_id}/splats?revision=0")
        assert manifest.status_code == 200
        assert manifest.json() == {
            "revision": 0,
            "graph_hash": graph_hash(graph),
            "assets": [{"url": f"/api/scans/{scan_id}/splats/room.ply?revision=0", "transform": IDENTITY}],
            "display_only": True,
        }
        assert manifest.headers["cache-control"] == "private, no-store"

        asset = client.get(f"/api/scans/{scan_id}/splats/room.ply?revision=0")
        assert asset.status_code == 200
        assert asset.content == b"ply fixture"
        assert asset.headers["cache-control"] == "private, no-store"

    with make_client(seed=True, sign_in_as_owner=False) as unsigned:
        assert unsigned.get(f"/api/scans/{scan_id}/splats?revision=0").status_code == 401


@pytest.mark.parametrize("field, value", [("revision", 1), ("graph_hash", "0" * 64)])
def test_stale_manifest_is_not_served(make_client, field, value):
    with make_client(seed=True) as client:
        scan_id, graph = _scan(client)
        _write_manifest(client, scan_id, graph, [{"filename": "room.spz", "transform": IDENTITY}])
        directory = client.app.state.store.scan_dir(uuid.UUID(scan_id)) / "revisions" / "0" / "splats"
        (directory / "room.spz").write_bytes(b"splat fixture")
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest[field] = value
        manifest_path.write_text(json.dumps(manifest))

        assert client.get(f"/api/scans/{scan_id}/splats?revision=0").status_code == 404
        assert client.get(f"/api/scans/{scan_id}/splats/room.spz?revision=0").status_code == 404


def test_manifest_rejects_path_escape_symlink_and_nonrigid_transform(make_client, tmp_path):
    with make_client(seed=True) as client:
        scan_id, graph = _scan(client)
        directory = client.app.state.store.scan_dir(uuid.UUID(scan_id)) / "revisions" / "0" / "splats"
        directory.mkdir(parents=True, exist_ok=True)
        outside = tmp_path / "outside.ply"
        outside.write_bytes(b"private")
        (directory / "link.ply").symlink_to(outside)

        manifest = {
            "revision": 0,
            "graph_hash": graph_hash(graph),
            "assets": [{"filename": "link.ply", "transform": IDENTITY}],
        }
        (directory / "manifest.json").write_text(json.dumps(manifest))
        assert client.get(f"/api/scans/{scan_id}/splats?revision=0").status_code == 404

        manifest["assets"] = [{"filename": "../outside.ply", "transform": IDENTITY}]
        (directory / "manifest.json").write_text(json.dumps(manifest))
        assert client.get(f"/api/scans/{scan_id}/splats?revision=0").status_code == 404

        manifest["assets"] = [{"filename": "room.ply", "transform": [2.0, *IDENTITY[1:]]}]
        (directory / "manifest.json").write_text(json.dumps(manifest))
        assert client.get(f"/api/scans/{scan_id}/splats?revision=0").status_code == 404

        with client.app.state.database.connect() as connection:
            assert repo.get_revision(connection, uuid.UUID(scan_id), 0) is not None
