"""Limits past a single scan: each owner's scans and bytes, the job queue, and the disk.

`store.ScanQuota` stops one scan filling the volume, but not an account that
opens scan after scan, and not a crowd of guests each opening one. These limits
come after it, and each says no before the work or the bytes are taken on.

The team is exempt from the per-owner limits. Its account holds every test walk
of the Moffitt library, about ten at up to 2.5 GB each, and the free-disk floor
protects the volume from it just as well.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass

from . import repository as repo
from .accounts import Owner
from .errors import ApiProblem
from .store import ArtifactStore, ScanFull

QUEUE_RETRY_SECONDS = 300
LOW_DISK = "The server is running low on storage, so it can't take new uploads right now. Try again later."
QUEUE_FULL = "Lots of shops are being measured right now. Your walk is saved, so try again in a few minutes."


@dataclass(frozen=True)
class Budgets:
    owner_scans: int
    owner_bytes: int
    queued_jobs: int
    min_free_disk_bytes: int

    def admit_scan(self, connection: sqlite3.Connection, store: ArtifactStore, owner: Owner) -> None:
        self.admit_disk(store, 0)
        if owner.team:
            return
        if repo.owner_scan_count(connection, owner.id) >= self.owner_scans:
            raise ApiProblem(403, f"This account already holds {self.owner_scans} scans. Delete one to make room.")

    def admit_owner_bytes(self, connection: sqlite3.Connection, owner: Owner, incoming_bytes: int) -> None:
        if owner.team:
            return
        if repo.owner_artifact_bytes(connection, owner.id) + incoming_bytes > self.owner_bytes:
            raise ApiProblem(413, f"this account would hold more than {self.owner_bytes} bytes")

    def admit_disk(self, store: ArtifactStore, incoming_bytes: int) -> None:
        """Keep the floor free for the worker's own output, which lands on the same volume."""
        if store.free_bytes() - incoming_bytes < self.min_free_disk_bytes:
            raise ApiProblem(507, LOW_DISK)

    def admit_queued_work(self, connection: sqlite3.Connection) -> None:
        if repo.queued_job_count(connection) >= self.queued_jobs:
            raise ApiProblem(503, QUEUE_FULL, headers={"Retry-After": str(QUEUE_RETRY_SECONDS)})


@dataclass(frozen=True)
class UploadAdmission:
    """Every limit one artifact upload answers to: its scan's quota, its owner's budget and the disk."""

    budgets: Budgets
    store: ArtifactStore
    owner: Owner
    scan_id: uuid.UUID

    def before_reading(self, connection: sqlite3.Connection, declared_bytes: int) -> None:
        """What can be refused from the headers alone, so a doomed body is never streamed to disk."""
        self.budgets.admit_disk(self.store, declared_bytes)
        self._admit(connection, declared_bytes)

    def before_storing(self, connection: sqlite3.Connection, staged_bytes: int) -> None:
        """The staged bytes are on disk already, so the floor is checked with nothing more to come."""
        self.budgets.admit_disk(self.store, 0)
        self._admit(connection, staged_bytes)

    def _admit(self, connection: sqlite3.Connection, incoming_bytes: int) -> None:
        try:
            self.store.quota.admit(*repo.artifact_usage(connection, self.scan_id), incoming_bytes)
        except ScanFull as full:
            raise ApiProblem(413, str(full)) from None
        self.budgets.admit_owner_bytes(connection, self.owner, incoming_bytes)
