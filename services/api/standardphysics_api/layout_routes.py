"""Editing a scan's layout: combining rooms, checking and saving layouts, the fix loops and relabelling."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator
from standardphysics_contracts import (
    AskAnswer,
    AskRequest,
    LayoutCheckRequest,
    LayoutCheckResult,
    LoopRequest,
    LoopResult,
    ManualMarkRequest,
    ModelLoopInfo,
    ModelLoopRequest,
    ProposalRequest,
    ProposalResult,
    SaveLayoutRequest,
    SceneGraph,
)

from .auth import owner_of
from .combine import SaveCombineRequest, rooms_of, save_combine
from .db import Database
from .labels import (
    edit_object,
    mark_counter,
    mark_observation,
    remove_object,
    restore_object,
    review_outlet,
    unmark_counter,
)
from .layout import check_layout, save_layout
from .loop_run import run as run_loop_on
from .loop_run import stream as stream_loop_on
from .model_chooser import ModelSlots
from .model_loop import loop_info, stream_model_loop
from .proposals import propose
from .questions import answer_question
from .scan_routes import scan_or_404
from .stages import Stages
from .store import ArtifactStore
from .worker import Worker


def install_combine_routes(app: FastAPI, database: Database, store: ArtifactStore, worker: Worker) -> None:
    @app.get("/api/scans/{scan_id}/rooms")
    def rooms(scan_id: uuid.UUID, revision: int | None = None) -> dict:
        with database.connect() as connection:
            scan_or_404(connection, scan_id)
        return rooms_of(database, store, scan_id, revision)

    @app.post("/api/scans/{scan_id}/combine", response_model=SceneGraph, status_code=201)
    def combine(scan_id: uuid.UUID, body: SaveCombineRequest) -> SceneGraph:
        return save_combine(database, worker, scan_id, body)


def install_layout_routes(
    app: FastAPI, database: Database, stages: Stages, worker: Worker, model_slots: ModelSlots
) -> None:
    @app.post("/api/scans/{scan_id}/layout-checks", response_model=LayoutCheckResult)
    def layout_check(scan_id: uuid.UUID, body: LayoutCheckRequest) -> LayoutCheckResult:
        return check_layout(database, stages, scan_id, body)

    @app.post("/api/scans/{scan_id}/ask", response_model=AskAnswer)
    def ask_about_the_shop(scan_id: uuid.UUID, body: AskRequest) -> AskAnswer:
        return answer_question(database, stages, scan_id, body)

    @app.post("/api/scans/{scan_id}/loop", response_model=LoopResult)
    def fix_what_it_can(scan_id: uuid.UUID, body: LoopRequest) -> LoopResult:
        return run_loop_on(database, stages, scan_id, body)

    @app.post("/api/scans/{scan_id}/loop/stream")
    def fix_what_it_can_as_it_goes(scan_id: uuid.UUID, body: LoopRequest) -> StreamingResponse:
        lines = stream_loop_on(database, stages, scan_id, body)
        # no-transform stops a compressing proxy from holding lines back until the loop ends.
        headers = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
        return StreamingResponse(lines, media_type="application/x-ndjson", headers=headers)

    @app.get("/api/model-loop", response_model=ModelLoopInfo)
    def model_loop_info() -> ModelLoopInfo:
        return loop_info()

    @app.post("/api/scans/{scan_id}/model-loop/stream")
    def model_loop_stream(scan_id: uuid.UUID, body: ModelLoopRequest, request: Request) -> StreamingResponse:
        lines = stream_model_loop(database, stages, model_slots, owner_of(request).id, scan_id, body)
        headers = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
        return StreamingResponse(lines, media_type="application/x-ndjson", headers=headers)

    @app.post("/api/scans/{scan_id}/proposals", response_model=ProposalResult)
    def proposal(scan_id: uuid.UUID, body: ProposalRequest, request: Request) -> ProposalResult:
        return propose(database, stages, model_slots, owner_of(request).id, scan_id, body)

    @app.post("/api/scans/{scan_id}/revisions", response_model=SceneGraph, status_code=201)
    def save_revision(scan_id: uuid.UUID, body: SaveLayoutRequest) -> SceneGraph:
        return save_layout(database, worker, scan_id, body)


class ReviewOutletRequest(BaseModel):
    status: str


OwnerWord = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


class EditObjectRequest(BaseModel):
    """What the owner changed about a found piece: its name, the group it is listed under, or both."""

    model_config = ConfigDict(extra="forbid")
    label: OwnerWord | None = None
    group: OwnerWord | None = None

    @model_validator(mode="after")
    def changes_something(self) -> EditObjectRequest:
        if self.label is None and self.group is None:
            raise ValueError("say a new name or a group")
        return self


def install_label_routes(app: FastAPI, database: Database, store: ArtifactStore, worker: Worker) -> None:
    counter_path = "/api/scans/{scan_id}/revisions/{base_revision}/counters/{node_id}"

    @app.put(counter_path, response_model=SceneGraph, status_code=201)
    def mark_as_counter(scan_id: uuid.UUID, base_revision: int, node_id: uuid.UUID) -> SceneGraph:
        return mark_counter(database, worker, scan_id, base_revision, node_id)

    @app.delete(counter_path, response_model=SceneGraph, status_code=201)
    def unmark_as_counter(scan_id: uuid.UUID, base_revision: int, node_id: uuid.UUID) -> SceneGraph:
        return unmark_counter(database, worker, scan_id, base_revision, node_id)

    object_path = "/api/scans/{scan_id}/revisions/{base_revision}/objects/{node_id}"

    @app.put(object_path, response_model=SceneGraph, status_code=201)
    def change_found_object(
        scan_id: uuid.UUID, base_revision: int, node_id: uuid.UUID, body: EditObjectRequest
    ) -> SceneGraph:
        return edit_object(database, worker, scan_id, base_revision, node_id, body.label, body.group)

    @app.delete(object_path, response_model=SceneGraph, status_code=201)
    def remove_found_object(scan_id: uuid.UUID, base_revision: int, node_id: uuid.UUID) -> SceneGraph:
        return remove_object(database, worker, scan_id, base_revision, node_id)

    @app.put(f"{object_path}/restore", response_model=SceneGraph, status_code=201)
    def restore_found_object(
        scan_id: uuid.UUID, base_revision: int, node_id: uuid.UUID, from_revision: int
    ) -> SceneGraph:
        return restore_object(database, worker, scan_id, base_revision, node_id, from_revision)

    outlet_review_path = "/api/scans/{scan_id}/revisions/{base_revision}/outlets/{node_id}/review"

    @app.put(outlet_review_path, response_model=SceneGraph, status_code=201)
    def update_outlet_review(
        scan_id: uuid.UUID,
        base_revision: int,
        node_id: uuid.UUID,
        body: ReviewOutletRequest,
    ) -> SceneGraph:
        return review_outlet(database, worker, scan_id, base_revision, node_id, body.status)

    @app.put("/api/scans/{scan_id}/revisions/{base_revision}/observations",
             response_model=SceneGraph, status_code=201)
    def add_observation(
        scan_id: uuid.UUID,
        base_revision: int,
        body: ManualMarkRequest,
        request: Request,
    ) -> SceneGraph:
        """A person marks photo evidence for a target the pipeline did not find.

        The middleware settled ownership; the actor is the signed-in owner.
        A mark with a node attaches to that node's evidence; without one it is
        stored unlocalized on the graph, never given an invented position.
        """
        return mark_observation(
            database, store, worker, scan_id, base_revision, body, owner_of(request).email
        )
