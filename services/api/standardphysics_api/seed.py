"""The sample shop, entered into the pipeline just after ingest.

It carries the lawsuit variant of the fixture shop (the register on the 47 in
counter, as in Whitaker v. T Rock Inc.) with its human labels, its scenario and
the committed `shop_lawsuit.glb`, so a clean clone has a shop to open without Blender
or a key. From assess onward it takes the same path as a real scan.

The shop belongs to a demo owner, created alongside it, because a scan with no
owner is visible to nobody. `SP_SEED_OWNER_EMAIL` and `SP_SEED_OWNER_PASSWORD`
name that account; both are printed at startup so whoever ran the server can
sign in as it.
"""

from __future__ import annotations

import pathlib
import shutil
import sqlite3
import uuid

from standardphysics_contracts import CreateScanRequest
from standardphysics_fixtures import build_lawsuit_graph, build_lawsuit_scenario

from . import accounts
from . import repository as repo
from .db import Database
from .store import ArtifactStore
from .worker import ASSESS

SAMPLE_NAME = "Sample boba shop"
FIXTURE_GLB = pathlib.Path(__import__("standardphysics_fixtures").__file__).parent / "data" / "shop_lawsuit.glb"


def demo_owner(connection: sqlite3.Connection, email: str, password: str) -> uuid.UUID:
    """The demo account, created on first use and reused after that."""
    existing = connection.execute(
        "SELECT id FROM owners WHERE email = ?", (accounts.normalize_email(email),)
    ).fetchone()
    if existing is not None:
        return uuid.UUID(existing["id"])
    return accounts.register(connection, email, password, SAMPLE_NAME).id


def seed_sample_shop(database: Database, store: ArtifactStore, email: str, password: str) -> bool:
    graph = build_lawsuit_graph()
    with database.transaction() as connection:
        owner_id = demo_owner(connection, email, password)
        if repo.scan_exists(connection, graph.scan_id):
            return False
        request = CreateScanRequest(name=SAMPLE_NAME, device_model="Sample", duration_seconds=0.0)
        repo.insert_scan(connection, request, owner_id, scan_id=graph.scan_id, state="checking")
        glb = store.scan_dir(graph.scan_id) / "revisions" / "0" / "scene.glb"
        glb.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURE_GLB, glb)
        repo.save_revision(connection, graph, source="sample", glb_path=str(glb))
        repo.save_scenario(connection, graph.scan_id, build_lawsuit_scenario())
        repo.enqueue_job(connection, graph.scan_id, ASSESS, graph.revision)
    return True
