"""Shared helpers for the slice probes: blank-app stages, hashing and job draining."""

from __future__ import annotations

import hashlib
import pathlib

from fastapi.testclient import TestClient
from standardphysics_contracts.hashing import graph_hash  # noqa: F401  (import path probe)

from standardphysics_api.stages import Stages, preview_ledger

OUT_DIR = pathlib.Path("scripts/shop_pilot/assets/slices/i1")


def probe_stages() -> Stages:
    """Blender-free stages like the API test suite; detection still requires a key."""
    from standardphysics_pipeline import blender  # noqa: F401
    options = dict(ledger_factory=preview_ledger)
    return Stages(**options)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def drain_jobs(client: TestClient) -> list[str]:
    with client.app.state.database.connect() as connection:
        rows = connection.execute(
            "SELECT id, state FROM jobs ORDER BY id"
        ).fetchall()
        job_ids = [row["id"] for row in rows]
    client.app.state.worker.drain()
    return job_ids

