"""The owner's routes: requests and their answers, the checklist and the journey.

docs/UX.md has the table of routes. Everything under /api/scans is guarded by
the ownership middleware in auth.py. The journey list and the team's photo
reviews live outside that prefix, so each checks the caller itself.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from standardphysics_contracts import (
    AnswerRequest,
    Assessment,
    Checklist,
    ChecklistItem,
    ChecklistUpdate,
    Funnel,
    Journey,
    JourneyList,
    OwnerRequest,
    PendingReview,
    ReviewAnswer,
    ReviewQueue,
    Scan,
    ShopRequests,
)

from . import checklist as checklists
from . import owner_requests as asks
from . import repository as repo
from .answered_findings import apply_answers
from .auth import signed_in, team_member
from .db import Database
from .errors import ApiProblem
from .funnel import funnel
from .journey import ShopState, journey
from .notifications import Push
from .stages import Stages
from .store import ArtifactStore

SERVICE_COUNTER = "service counter"
MEDIA_TYPES = {"jpg": "image/jpeg", "png": "image/png"}


@dataclass(frozen=True)
class Shop:
    scan: Scan
    requests: list[OwnerRequest]
    assessment: Assessment | None
    """The latest assessment with the owner's answers laid over it."""


def load_shop(connection: sqlite3.Connection, stages: Stages, scan_id: uuid.UUID) -> Shop:
    scan = repo.get_scan(connection, scan_id)
    if scan is None:
        raise ApiProblem(404, "no scan")
    measured = repo.latest_assessment(connection, scan_id)
    requests = asks.owner_requests(connection, scan_id, asks.enabled_rule_ids(stages.ledger_factory()), measured)
    return Shop(scan, requests, apply_answers(measured, requests) if measured else None)


def answered(connection: sqlite3.Connection, stages: Stages, scan_id: uuid.UUID, found: Assessment) -> Assessment:
    """Any assessment of this shop, with the owner's answers laid over it."""
    measured = repo.latest_assessment(connection, scan_id)
    requests = asks.owner_requests(connection, scan_id, asks.enabled_rule_ids(stages.ledger_factory()), measured)
    return apply_answers(found, requests)


def _counter_marked(connection: sqlite3.Connection, scan_id: uuid.UUID) -> bool:
    row = repo.get_revision(connection, scan_id)
    if row is None:
        return False
    return any(
        node.labeled_by == "owner" and node.label == SERVICE_COUNTER for node in repo.graph_of(row).nodes
    )


def journey_of(connection: sqlite3.Connection, stages: Stages, scan_id: uuid.UUID) -> Journey:
    shop = load_shop(connection, stages, scan_id)
    return journey(ShopState(
        scan=shop.scan,
        shop_name=shop.scan.name,
        requests=shop.requests,
        assessment=shop.assessment,
        measured=repo.get_revision(connection, scan_id) is not None,
        counter_marked=_counter_marked(connection, scan_id),
        path_confirmed=repo.get_scenario(connection, scan_id) is not None,
        checklist=checklists.checklist(connection, scan_id, shop.assessment),
    ))


def _photo_path(store: ArtifactStore, scan_id: uuid.UUID, request_id: str, extension: str):
    return store.scan_dir(scan_id) / "requests" / f"{request_id}.{extension}"


async def _read_photo(request: Request) -> bytes:
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > asks.MAX_PHOTO_BYTES:
            raise ApiProblem(413, "That photo is too large. Send one under 15 MB.")
        chunks.append(chunk)
    return b"".join(chunks)


def _save_photo(store: ArtifactStore, scan_id: uuid.UUID, request_id: str, body: bytes) -> str:
    extension = asks.photo_extension(body[:8])
    path = _photo_path(store, scan_id, request_id, extension)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".upload")
    staged.write_bytes(body)
    os.replace(staged, path)
    return path.name


def _photo_response(store: ArtifactStore, scan_id: uuid.UUID, request: OwnerRequest) -> FileResponse:
    for extension, media_type in MEDIA_TYPES.items():
        path = _photo_path(store, scan_id, request.id, extension)
        if path.exists():
            return FileResponse(path, media_type=media_type)
    raise ApiProblem(404, "no photo")


def install_owner_routes(
    app: FastAPI, database: Database, store: ArtifactStore, stages: Stages, team_emails: frozenset[str]
) -> None:
    _install_request_routes(app, database, store, stages)
    _install_progress_routes(app, database, stages)
    _install_review_routes(app, database, store, stages, team_emails)


def _install_request_routes(app: FastAPI, database: Database, store: ArtifactStore, stages: Stages) -> None:
    def one(scan_id: uuid.UUID, request_id: str) -> OwnerRequest:
        with database.connect() as connection:
            return asks.find(load_shop(connection, stages, scan_id).requests, request_id)

    @app.get("/api/scans/{scan_id}/requests", response_model=ShopRequests)
    def requests(scan_id: uuid.UUID) -> ShopRequests:
        with database.connect() as connection:
            return ShopRequests(requests=load_shop(connection, stages, scan_id).requests)

    @app.put("/api/scans/{scan_id}/requests/{request_id}/answer", response_model=OwnerRequest)
    def answer(scan_id: uuid.UUID, request_id: str, body: AnswerRequest) -> OwnerRequest:
        with database.transaction() as connection:
            found = asks.find(load_shop(connection, stages, scan_id).requests, request_id)
            asks.record_answer(connection, scan_id, found, body)
        return one(scan_id, request_id)

    @app.put("/api/scans/{scan_id}/requests/{request_id}/photo", response_model=OwnerRequest)
    async def photo(scan_id: uuid.UUID, request_id: str, request: Request) -> OwnerRequest:
        found = one(scan_id, request_id)
        body = await _read_photo(request)
        name = _save_photo(store, scan_id, found.id, body)
        with database.transaction() as connection:
            asks.record_photo(connection, scan_id, found, name)
        return one(scan_id, request_id)

    @app.post("/api/scans/{scan_id}/requests/{request_id}/skip", response_model=OwnerRequest)
    def skip(scan_id: uuid.UUID, request_id: str) -> OwnerRequest:
        with database.transaction() as connection:
            found = asks.find(load_shop(connection, stages, scan_id).requests, request_id)
            asks.record_skip(connection, scan_id, found)
        return one(scan_id, request_id)

    @app.get("/api/scans/{scan_id}/requests/{request_id}/photo")
    def sent_photo(scan_id: uuid.UUID, request_id: str) -> FileResponse:
        return _photo_response(store, scan_id, one(scan_id, request_id))


def _install_progress_routes(app: FastAPI, database: Database, stages: Stages) -> None:
    @app.get("/api/scans/{scan_id}/checklist", response_model=Checklist)
    def checklist(scan_id: uuid.UUID) -> Checklist:
        with database.connect() as connection:
            return checklists.checklist(connection, scan_id, load_shop(connection, stages, scan_id).assessment)

    @app.put("/api/scans/{scan_id}/checklist/{finding_id}", response_model=ChecklistItem)
    def mark(scan_id: uuid.UUID, finding_id: uuid.UUID, body: ChecklistUpdate) -> ChecklistItem:
        with database.transaction() as connection:
            shop = load_shop(connection, stages, scan_id)
            return checklists.mark(connection, scan_id, shop.assessment, finding_id, body.status)

    @app.get("/api/scans/{scan_id}/journey", response_model=Journey)
    def shop_journey(scan_id: uuid.UUID) -> Journey:
        with database.connect() as connection:
            return journey_of(connection, stages, scan_id)

    @app.get("/api/journeys", response_model=JourneyList)
    def journeys(request: Request) -> JourneyList:
        owner = signed_in(database, request)
        with database.connect() as connection:
            scans = repo.list_shops(connection, owner.id)
            return JourneyList(journeys=[journey_of(connection, stages, scan.id) for scan in scans])


def _team_view(scan_id: uuid.UUID, request: OwnerRequest) -> OwnerRequest:
    """The same request, with its photo at the team's own address."""
    if request.answer is None or request.answer.photo_url is None:
        return request
    url = f"/api/team/reviews/{scan_id}/{request.id}/photo"
    return request.model_copy(update={"answer": request.answer.model_copy(update={"photo_url": url})})


def _tell_owner(app: FastAPI, database: Database, stages: Stages, scan_id: uuid.UUID, request: OwnerRequest) -> None:
    """A push with what the photo showed, in the words the checklist uses."""
    with database.connect() as connection:
        owner_id = repo.scan_owner(connection, scan_id)
        shop = load_shop(connection, stages, scan_id)
    findings = shop.assessment.findings if shop.assessment else []
    result = next((finding for finding in findings if finding.id == request.finding_id), None)
    if owner_id is None or result is None:
        return
    app.state.notifier.send(database, owner_id, Push(title="We checked your photo", body=result.title, scan_id=scan_id))


def _install_review_routes(
    app: FastAPI, database: Database, store: ArtifactStore, stages: Stages, team_emails: frozenset[str]
) -> None:
    def reviewable(scan_id: uuid.UUID, request_id: str) -> OwnerRequest:
        with database.connect() as connection:
            return asks.find(load_shop(connection, stages, scan_id).requests, request_id)

    @app.get("/api/team/reviews", response_model=ReviewQueue)
    def queue(request: Request) -> ReviewQueue:
        team_member(database, team_emails, request)
        with database.connect() as connection:
            waiting = asks.pending_reviews(connection)
            reviews = []
            for scan_id, request_id in waiting:
                shop = load_shop(connection, stages, scan_id)
                found = asks.find(shop.requests, request_id)
                view = _team_view(scan_id, found)
                reviews.append(PendingReview(scan_id=scan_id, shop_name=shop.scan.name, request=view))
        return ReviewQueue(reviews=reviews)

    @app.get("/api/team/reviews/{scan_id}/{request_id}/photo")
    def review_photo(scan_id: uuid.UUID, request_id: str, request: Request) -> FileResponse:
        team_member(database, team_emails, request)
        return _photo_response(store, scan_id, reviewable(scan_id, request_id))

    @app.get("/api/team/funnel", response_model=Funnel)
    def owner_funnel(request: Request) -> Funnel:
        team_member(database, team_emails, request)
        with database.connect() as connection:
            return funnel(connection)

    @app.put("/api/team/reviews/{scan_id}/{request_id}", response_model=OwnerRequest)
    def review(scan_id: uuid.UUID, request_id: str, body: ReviewAnswer, request: Request) -> OwnerRequest:
        reviewer = team_member(database, team_emails, request)
        with database.transaction() as connection:
            found = asks.find(load_shop(connection, stages, scan_id).requests, request_id)
            asks.record_review(connection, scan_id, found, body.outcome, reviewer.email)
        _tell_owner(app, database, stages, scan_id, found)
        return _team_view(scan_id, reviewable(scan_id, request_id))


