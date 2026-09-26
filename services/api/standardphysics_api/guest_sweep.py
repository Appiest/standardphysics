"""Deleting guest shops nobody opened for 30 days, and the reminder 3 days before.

A guest never gave us an email, so a shop left behind by one would otherwise be
kept forever. The owner hears about it first: a push three days ahead, and the
deletion date in their session, which the app and the web show as a banner.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from . import accounts
from . import repository as repo
from .accounts import Owner
from .db import Database
from .notifications import Notifier, Push
from .store import ArtifactStore

log = logging.getLogger(__name__)

REMIND_BEFORE = timedelta(days=3)
DATE = "%b %-d"


def _guests(database: Database) -> list[tuple[Owner, datetime, datetime | None]]:
    with database.connect() as connection:
        rows = connection.execute("SELECT * FROM owners WHERE guest = 1").fetchall()
        guests = []
        for row in rows:
            owner = accounts.owner_from_row(row)
            reminded = datetime.fromisoformat(row["reminded_at"]) if row["reminded_at"] else None
            guests.append((owner, accounts.guest_deletes_at(connection, owner), reminded))
    return guests


def erase(database: Database, store: ArtifactStore, owner_id: uuid.UUID) -> None:
    with database.transaction() as connection:
        scan_ids = [scan.id for scan in repo.list_scans(connection, owner_id)]
        for scan_id in scan_ids:
            repo.delete_scan(connection, scan_id)
        accounts.delete_owner(connection, owner_id)
    for scan_id in scan_ids:
        store.remove_scan(scan_id)


def _remind(database: Database, notifier: Notifier, owner: Owner, deletes_at: datetime, now: datetime) -> None:
    with database.transaction() as connection:
        connection.execute("UPDATE owners SET reminded_at = ? WHERE id = ?", (now.isoformat(), str(owner.id)))
    notifier.send(database, owner.id, Push(
        title="Save your shop to keep it",
        body=f"Your shop will be deleted on {deletes_at.strftime(DATE)}. Open Standard Physics and save it.",
    ))


def sweep(database: Database, store: ArtifactStore, notifier: Notifier, now: datetime) -> None:
    for owner, deletes_at, reminded in _guests(database):
        if deletes_at <= now:
            log.info("deleting guest %s, whose shops nobody opened for 30 days", owner.id)
            erase(database, store, owner.id)
        elif now >= deletes_at - REMIND_BEFORE and (reminded is None or reminded < deletes_at - REMIND_BEFORE):
            _remind(database, notifier, owner, deletes_at, now)
