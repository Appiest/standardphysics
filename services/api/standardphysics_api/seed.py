"""The sample shop, entered into the pipeline just after ingest.

It carries the lawsuit variant of the fixture shop (the register on the 47 in
counter, as in Whitaker v. T Rock Inc.) with its human labels, its scenario and
the committed `shop_lawsuit.glb`, so a clean clone has a shop to open without Blender
or a key. From assess onward it takes the same path as a real scan.

The shop belongs to a demo owner, created alongside it, because a scan with no
owner is visible to nobody. `SP_SEED_OWNER_EMAIL` and `SP_SEED_OWNER_PASSWORD`
name that account. The password only takes effect on the run that creates the
account, so only that run prints it; later runs leave the stored password alone.
"""

from __future__ import annotations

import pathlib
import shutil
import sqlite3
import uuid

import standardphysics_fixtures
from standardphysics_contracts import CreateScanRequest
from standardphysics_fixtures import build_lawsuit_graph, build_lawsuit_scenario

from . import accounts
from . import repository as repo
from . import repository_jobs as jobs_repo
from . import repository_revisions as revisions_repo
from .db import Database
from .store import ArtifactStore
from .worker_handlers import ASSESS

SAMPLE_NAME = "Sample boba shop"
FIXTURE_GLB = pathlib.Path(standardphysics_fixtures.__path__[0]) / "data" / "shop_lawsuit.glb"


def demo_owner(connection: sqlite3.Connection, email: str, password: str) -> tuple[uuid.UUID, bool]:
    """The demo account and whether this call created it. An existing account
    keeps its own password; `password` is only used to register a new one."""
    existing = connection.execute(
        "SELECT id FROM owners WHERE email = ?", (accounts.normalize_email(email),)
    ).fetchone()
    if existing is not None:
        return uuid.UUID(existing["id"]), False
    return accounts.register(connection, email, password, SAMPLE_NAME).id, True


def seed_sample_shop(database: Database, store: ArtifactStore, email: str, password: str) -> bool:
    """Seed the sample shop once. True when this call created the demo owner,
    which is the only case where `password` is the one that signs in."""
    graph = build_lawsuit_graph()
    with database.transaction() as connection:
        owner_id, created_owner = demo_owner(connection, email, password)
        if repo.scan_exists(connection, graph.scan_id):
            return created_owner
        request = CreateScanRequest(name=SAMPLE_NAME, device_model="Sample", duration_seconds=0.0)
        repo.insert_scan(connection, request, owner_id, scan_id=graph.scan_id, state="checking")
        glb = store.scan_dir(graph.scan_id) / "revisions" / "0" / "scene.glb"
        glb.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURE_GLB, glb)
        revisions_repo.save_revision(connection, graph, source="sample", glb_path=str(glb))
        revisions_repo.save_scenario(connection, graph.scan_id, build_lawsuit_scenario())
        jobs_repo.enqueue_job(connection, graph.scan_id, ASSESS, graph.revision)
    return created_owner
