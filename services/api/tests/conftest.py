import hashlib
import pathlib
import shutil
import uuid

import pytest
from fastapi.testclient import TestClient
from standardphysics_pipeline import blender

from standardphysics_api.app import create_app
from standardphysics_api.settings import Settings
from standardphysics_api.stages import Stages, preview_ledger

REPO = pathlib.Path(__file__).resolve().parents[3]
FIXTURE_DATA = REPO / "packages/fixtures/standardphysics_fixtures/data"
PNG = b"\x89PNG\r\n\x1a\n"


def no_blender_stages(**overrides) -> Stages:
    """Every Lane B and C stage is real except the ones that launch Blender."""

    def export_glb(graph, out: pathlib.Path) -> pathlib.Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURE_DATA / "shop.glb", out)
        return out

    def usdz_to_glb(usdz, out, mapping):
        raise blender.BlenderError("no Blender in tests")

    def render_finding(graph, locus, out: pathlib.Path) -> pathlib.Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(PNG)
        return out

    options = dict(
        ledger_factory=preview_ledger, export_glb=export_glb,
        usdz_to_glb=usdz_to_glb, render_finding=render_finding,
    )
    return Stages(**{**options, **overrides})


OWNER_EMAIL = "owner@example.com"
OWNER_PASSWORD = "a-long-enough-password"
SEED_OWNER_PASSWORD = "seed-owner-password"


def sign_up(test_client: TestClient, email: str = OWNER_EMAIL, password: str = OWNER_PASSWORD) -> str:
    """Register an owner and leave this client signed in as them."""
    response = test_client.post(
        "/api/auth/sign-up", json={"email": email, "password": password, "shop_name": "Corner cafe"}
    )
    assert response.status_code == 201, response.text
    return response.json()["owner_id"]


def sign_in(test_client: TestClient, email: str, password: str) -> None:
    response = test_client.post("/api/auth/sign-in", json={"email": email, "password": password})
    assert response.status_code == 200, response.text


def sign_out(test_client: TestClient) -> None:
    assert test_client.post("/api/auth/sign-out").status_code == 204


@pytest.fixture
def make_client(tmp_path):
    def build(
        seed: bool = False,
        stages: Stages | None = None,
        sign_in_as_owner: bool = True,
        **settings_overrides,
    ) -> TestClient:
        settings = Settings(
            data_dir=tmp_path / "var",
            seed_sample_shop=seed,
            max_artifact_bytes=5_000_000,
            seed_owner_password=SEED_OWNER_PASSWORD,
            **settings_overrides,
        )
        test_client = TestClient(create_app(settings, stages or no_blender_stages(), run_worker=False))
        if not sign_in_as_owner:
            return test_client
        with test_client:
            if seed:
                sign_in(test_client, settings.seed_owner_email, SEED_OWNER_PASSWORD)
            else:
                sign_up(test_client)
        return test_client

    return build


@pytest.fixture
def client(make_client):
    with make_client() as test_client:
        yield test_client


@pytest.fixture
def stranger(make_client):
    """A signed-in owner with no scans of their own."""
    with make_client(sign_in_as_owner=False) as test_client:
        sign_up(test_client, "stranger@example.com", "another-long-password")
        yield test_client


def drain(test_client: TestClient) -> None:
    test_client.app.state.worker.drain()


def create_scan(test_client: TestClient) -> str:
    response = test_client.post(
        "/api/scans", json={"name": "Corner cafe", "device_model": "iPhone17,1", "duration_seconds": 142.5}
    )
    assert response.status_code == 201
    return response.json()["id"]


def put_artifact(test_client, scan_id, artifact_id, body: bytes, kind: str, checksum: str | None = None):
    return test_client.put(
        f"/api/scans/{scan_id}/artifacts/{artifact_id}",
        content=body,
        headers={"X-Checksum-SHA256": checksum or hashlib.sha256(body).hexdigest(), "X-Artifact-Kind": kind},
    )


def unknown_scan() -> str:
    return str(uuid.uuid4())
