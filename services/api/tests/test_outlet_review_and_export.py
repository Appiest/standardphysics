"""Tests for outlet review persistence, concurrency, and architectural export."""

from __future__ import annotations

import io
import json
import uuid
import zipfile

import pytest
from standardphysics_contracts import (
    Mat4,
    ObservationCrop,
    SceneGraph,
    SceneNode,
    SocketTarget,
    SurfaceAttachment,
    Vec3,
)

from conftest import drain
from standardphysics_api import repository_revisions as revisions_repo
from standardphysics_api.architecture_export import build_architecture_zip


def _make_graph_with_outlet(scan_id: uuid.UUID, rev: int = 0) -> tuple[SceneGraph, SceneNode]:
    floor = SceneNode(
        id=uuid.uuid4(),
        kind="floor",
        label="Floor",
        raw_category="floor",
        dimensions=Vec3(x=5.0, y=5.0, z=0.05),
        transform=Mat4(m=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0.10, 0, 0, 0, 1]),
    )
    wall = SceneNode(
        id=uuid.uuid4(),
        kind="wall",
        label="Wall",
        raw_category="wall",
        dimensions=Vec3(x=5.0, y=0.2, z=3.0),
        transform=Mat4(m=[1, 0, 0, 0, 0, 1, 0, 2.0, 0, 0, 1, 1.5, 0, 0, 0, 1]),
    )
    outlet = SceneNode(
        id=uuid.uuid4(),
        kind="outlet",
        label="Wall Outlet",
        raw_category="outlet",
        dimensions=Vec3(x=0.07, y=0.02, z=0.115),
        transform=Mat4(m=[1, 0, 0, 0, 0, 1, 0, 1.9, 0, 0, 1, 0.55, 0, 0, 0, 1]),
        parent_id=wall.id,
        relation="attached_to",
        attachment=SurfaceAttachment(
            support_type="lidar_surface",
            support_node_id=wall.id,
            normal=Vec3(x=0.0, y=-1.0, z=0.0),
            review_status="detected",
            observations=[
                ObservationCrop(
                    frame_id="frame-01",
                    sensor_box=[100.0, 100.0, 200.0, 200.0],
                    confidence=0.92,
                    image_url="crops/crop-01.jpg",
                )
            ],
            sockets=[
                SocketTarget(id="socket-0", center=Vec3(x=0.0, y=1.9, z=0.58)),
            ],
        ),
    )
    graph = SceneGraph(scan_id=scan_id, revision=rev, nodes=[floor, wall, outlet])
    return graph, outlet


def test_persist_01_owner_outlet_review_increments_revision_and_updates_status(make_client):
    """PERSIST-01: Owner outlet review produces new layout revision with incremented sequence and updated node status."""
    with make_client(seed=True) as client:
        drain(client)
        scan_id = uuid.UUID(client.get("/api/scans").json()["scans"][0]["id"])

        # Inject an outlet into current revision
        app = client.app
        database = app.state.database
        with database.connect() as conn:
            row = revisions_repo.get_revision(conn, scan_id)
            base_rev = row["revision"]
            base_graph = revisions_repo.graph_of(row)

        wall = next(n for n in base_graph.nodes if n.kind == "wall")
        outlet = SceneNode(
            id=uuid.uuid4(),
            kind="outlet",
            label="Wall Outlet",
            raw_category="outlet",
            dimensions=Vec3(x=0.07, y=0.02, z=0.115),
            transform=Mat4(m=[1, 0, 0, 0, 0, 1, 0, 1.9, 0, 0, 1, 0.55, 0, 0, 0, 1]),
            parent_id=wall.id,
            relation="attached_to",
            attachment=SurfaceAttachment(
                support_type="lidar_surface",
                support_node_id=wall.id,
                normal=Vec3(x=0.0, y=-1.0, z=0.0),
                review_status="detected",
                observations=[
                    ObservationCrop(
                        frame_id="frame-01",
                        sensor_box=[100.0, 100.0, 200.0, 200.0],
                        confidence=0.92,
                        image_url="crops/crop-01.jpg",
                    )
                ],
                sockets=[
                    SocketTarget(id="socket-0", center=Vec3(x=0.0, y=1.9, z=0.58)),
                ],
            ),
        )
        outlet_graph = base_graph.model_copy(update={"nodes": [*base_graph.nodes, outlet], "revision": base_rev + 1})



        with database.transaction() as conn:
            revisions_repo.save_revision(conn, outlet_graph, source="owner", base_revision=base_rev)

        # Call review endpoint against base_rev + 1
        url = f"/api/scans/{scan_id}/revisions/{base_rev + 1}/outlets/{outlet.id}/review"
        res = client.put(url, json={"status": "confirmed_by_user"})
        assert res.status_code == 201, res.text
        data = res.json()
        assert data["revision"] == base_rev + 2

        rev_node = next(n for n in data["nodes"] if n["id"] == str(outlet.id))
        assert rev_node["attachment"]["review_status"] == "confirmed_by_user"
        assert rev_node["labeled_by"] == "owner"

        # Verify DB persisted
        with database.connect() as conn:
            latest_row = revisions_repo.get_revision(conn, scan_id)
            assert latest_row["revision"] == base_rev + 2
            assert latest_row["source"] == "owner"


def test_rev_01_stale_revision_returns_409_conflict(make_client):
    """REV-01: Conflicting base revision returns 409 conflict and prevents silent overwrite."""
    with make_client(seed=True) as client:
        drain(client)
        scan_id = uuid.UUID(client.get("/api/scans").json()["scans"][0]["id"])

        app = client.app
        database = app.state.database
        with database.connect() as conn:
            row = revisions_repo.get_revision(conn, scan_id)
            base_rev = row["revision"]
            base_graph = revisions_repo.graph_of(row)

        wall = next(n for n in base_graph.nodes if n.kind == "wall")
        outlet = SceneNode(
            id=uuid.uuid4(),
            kind="outlet",
            label="Wall Outlet",
            raw_category="outlet",
            dimensions=Vec3(x=0.07, y=0.02, z=0.115),
            transform=Mat4(m=[1, 0, 0, 0, 0, 1, 0, 1.9, 0, 0, 1, 0.55, 0, 0, 0, 1]),
            parent_id=wall.id,
            relation="attached_to",
            attachment=SurfaceAttachment(
                support_type="lidar_surface",
                support_node_id=wall.id,
                normal=Vec3(x=0.0, y=-1.0, z=0.0),
                review_status="detected",
                observations=[
                    ObservationCrop(
                        frame_id="frame-01",
                        sensor_box=[100.0, 100.0, 200.0, 200.0],
                        confidence=0.92,
                        image_url="crops/crop-01.jpg",
                    )
                ],
                sockets=[
                    SocketTarget(id="socket-0", center=Vec3(x=0.0, y=1.9, z=0.58)),
                ],
            ),
        )
        outlet_graph = base_graph.model_copy(update={"nodes": [*base_graph.nodes, outlet], "revision": base_rev + 1})


        with database.transaction() as conn:
            revisions_repo.save_revision(conn, outlet_graph, source="owner", base_revision=base_rev)

        # Advance revision to base_rev + 2
        url = f"/api/scans/{scan_id}/revisions/{base_rev + 1}/outlets/{outlet.id}/review"
        res1 = client.put(url, json={"status": "confirmed_by_user"})
        assert res1.status_code == 201

        # Trying to submit against base_rev + 1 again must return 409 Conflict (stale)
        res2 = client.put(url, json={"status": "rejected_by_user"})
        assert res2.status_code == 409
        assert "stale" in res2.json().get("error", "").lower() or res2.status_code == 409


def test_export_01_includes_local_floor_height_review_status_and_crop(make_client):
    """EXPORT-01: Architecture export includes outlets with correct local floor height, review status, and crop reference."""
    scan_id = uuid.uuid4()
    graph, outlet = _make_graph_with_outlet(scan_id, rev=2)
    # Outlet world z is 0.55, floor elevation is 0.10 -> local height is 0.45m
    zip_bytes = build_architecture_zip(scan_id, "Shop with local floor", graph)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        assert "outlets.json" in archive.namelist()
        outlets_data = json.loads(archive.read("outlets.json"))
        assert len(outlets_data["outlets"]) == 1
        out = outlets_data["outlets"][0]
        assert out["id"] == str(outlet.id)
        # Verify local floor height is 0.45m (0.55 - 0.10)
        assert out["local_height_m"] == pytest.approx(0.45, abs=1e-3)
        assert out["review_status"] == "detected"
        assert out["crop_reference"] == "crops/crop-01.jpg"


def test_auth_02_architecture_zip_requires_scan_ownership(make_client, stranger):
    """AUTH-02: Architecture zip requires scan ownership; non-owner gets 404 or 403."""
    with make_client(seed=True) as client:
        drain(client)
        scan_id = client.get("/api/scans").json()["scans"][0]["id"]

        # Owner succeeds
        res_owner = client.get(f"/api/scans/{scan_id}/architecture.zip")
        assert res_owner.status_code == 200

        # Stranger gets 404
        res_stranger = stranger.get(f"/api/scans/{scan_id}/architecture.zip")
        assert res_stranger.status_code in (404, 403)

    # Unauthenticated gets 401
    with make_client(sign_in_as_owner=False) as anon_client:
        res_anon = anon_client.get(f"/api/scans/{scan_id}/architecture.zip")
        assert res_anon.status_code == 401
