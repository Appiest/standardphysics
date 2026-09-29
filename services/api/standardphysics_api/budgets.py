"""Limits past a single scan: each owner's scans and bytes, the job queue, and the disk.

`store.ScanQuota` stops one scan filling the volume, but not an account that
opens scan after scan, and not a crowd of guests each opening one. These limits
come after it, and each says no before the work or the bytes are taken on.

The team is exempt from the per-owner limits. Its account holds every test walk
of the Moffitt library, about ten at up to 2.5 GB each, and the free-disk floor
protects the volume from it just as well.

An upload that is still streaming is in neither the database nor, fully, on the
disk, so each one holds a reservation of the bytes it declared from admission
until it is stored or refused. Admission counts every reservation held, so two
uploads that each fit but together don't are not both taken.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field

from . import drain
from . import repository as repo
from . import repository_jobs as jobs_repo
from .accounts import Owner
from .errors import ApiProblem
from .store import ArtifactStore, ScanFull

QUEUE_RETRY_SECONDS = 300
LOW_DISK = "The server is running low on storage, so it can't take new uploads right now. Try again later."
QUEUE_FULL = "Lots of shops are being measured right now. Your walk is saved, so try again in a few minutes."
QUEUE_BUSY = "Lots of shops are being measured right now, so this can't be checked yet. Try again in a few minutes."
UPLOAD_RETRY_SECONDS = 30


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
        admit_new_job(connection, self.queued_jobs, QUEUE_FULL)


def admit_new_job(connection: sqlite3.Connection, max_queued_jobs: int | None, refusal: str = QUEUE_BUSY) -> None:
    """Refuse a job someone is asking for while `max_queued_jobs` are already waiting, with a time to retry.

    Call it in the transaction that queues the job, so two requests can't both take the last place.
    `None` is work a finished job queues for itself, such as the checks after a measurement, which is
    never refused: turning it away would leave a shop half done with nobody left to ask again.
    While a deploy drains the API every other job is refused, so the restart has nothing new to interrupt.
    """
    if max_queued_jobs is None:
        return
    drain.refuse_new_work(connection)
    if jobs_repo.queued_job_count(connection) >= max_queued_jobs:
        raise ApiProblem(503, refusal, headers={"Retry-After": str(QUEUE_RETRY_SECONDS)})


@dataclass(frozen=True)
class InFlight:
    """What uploads already streaming will still add: to the disk, and to one owner's stored bytes."""

    disk_bytes: int = 0
    owner_bytes: int = 0


@dataclass(eq=False)
class Reservation:
    owner_id: uuid.UUID
    declared_bytes: int
    arrived_bytes: int = 0

    def still_to_arrive(self) -> int:
        return max(self.declared_bytes - self.arrived_bytes, 0)

    async def counted(self, chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        """Pass the body through, keeping count, since bytes already written show up in the disk's free space."""
        async for chunk in chunks:
            self.arrived_bytes += len(chunk)
            yield chunk


@dataclass
class UploadReservations:
    """Every upload streaming in this process, capped per owner and in total."""

    per_owner: int
    total: int
    _held: list[Reservation] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @contextlib.contextmanager
    def hold(
        self, owner_id: uuid.UUID, declared_bytes: int, admit: Callable[[InFlight], None]
    ) -> Iterator[Reservation]:
        """Reserve `declared_bytes` if `admit` accepts them on top of what is in flight, until the block ends."""
        reservation = Reservation(owner_id, declared_bytes)
        with self._lock:
            self._admit_another(owner_id)
            admit(self._in_flight(owner_id))
            self._held.append(reservation)
        try:
            yield reservation
        finally:
            with self._lock:
                self._held.remove(reservation)

    def _admit_another(self, owner_id: uuid.UUID) -> None:
        retry = {"Retry-After": str(UPLOAD_RETRY_SECONDS)}
        if sum(held.owner_id == owner_id for held in self._held) >= self.per_owner:
            raise ApiProblem(429, f"This account is already sending {self.per_owner} files at once. "
                             "Try this one again when one of them finishes.", headers=retry)
        if len(self._held) >= self.total:
            raise ApiProblem(503, "The server is taking lots of uploads right now. Try again in a moment.",
                             headers=retry)

    def _in_flight(self, owner_id: uuid.UUID) -> InFlight:
        return InFlight(
            disk_bytes=sum(held.still_to_arrive() for held in self._held),
            owner_bytes=sum(held.declared_bytes for held in self._held if held.owner_id == owner_id),
        )


@dataclass(frozen=True)
class UploadAdmission:
    """Every limit one artifact upload answers to: its scan's quota, its owner's budget, the disk,
    and how many uploads are streaming already."""

    budgets: Budgets
    store: ArtifactStore
    owner: Owner
    scan_id: uuid.UUID
    reservations: UploadReservations

    def before_reading(self, connection: sqlite3.Connection, declared_bytes: int, in_flight: InFlight) -> None:
        """What can be refused from the headers alone, so a doomed body is never streamed to disk.

        The uploads already streaming are counted as if they had landed, since they were admitted first.
        """
        self.budgets.admit_disk(self.store, declared_bytes + in_flight.disk_bytes)
        self._admit(connection, declared_bytes, in_flight.owner_bytes)

    def before_storing(self, connection: sqlite3.Connection, staged_bytes: int) -> None:
        """The staged bytes are on disk already, so the floor is checked with nothing more to come."""
        self.budgets.admit_disk(self.store, 0)
        self._admit(connection, staged_bytes, 0)

    def _admit(self, connection: sqlite3.Connection, incoming_bytes: int, owner_in_flight: int) -> None:
        try:
            self.store.quota.admit(*repo.artifact_usage(connection, self.scan_id), incoming_bytes)
        except ScanFull as full:
            raise ApiProblem(413, str(full)) from None
        self.budgets.admit_owner_bytes(connection, self.owner, incoming_bytes + owner_in_flight)


@dataclass(frozen=True)
class PhotoAdmission:
    """The limits an answer photo answers to: its owner's budget, the disk, and how many uploads are
    streaming already. A photo is not an artifact, so the scan's artifact quota doesn't count it; each
    request keeps one photo, which caps a scan's photos at one per request."""

    budgets: Budgets
    store: ArtifactStore
    owner: Owner
    reservations: UploadReservations

    def before_reading(self, connection: sqlite3.Connection, declared_bytes: int, in_flight: InFlight) -> None:
        self.budgets.admit_disk(self.store, declared_bytes + in_flight.disk_bytes)
        self.budgets.admit_owner_bytes(connection, self.owner, declared_bytes + in_flight.owner_bytes)

    def before_storing(self, connection: sqlite3.Connection, staged_bytes: int) -> None:
        self.budgets.admit_disk(self.store, 0)
        self.budgets.admit_owner_bytes(connection, self.owner, staged_bytes)
