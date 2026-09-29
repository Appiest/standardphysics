"""The files a scan serves: display geometry, the LiDAR mesh, finding stills, crops and source frames."""

from __future__ import annotations

import io
import pathlib
import re
import uuid

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse
from PIL import Image as PILImage
from standardphysics_contracts import Artifact, FrameEntry, FrameListing
from standardphysics_contracts.textures import FRAME_ID_PATTERN

from . import repository as repo
from . import repository_jobs as jobs_repo
from . import repository_revisions as revisions_repo
from .db import Database
from .errors import ApiProblem
from .scan_routes import scan_or_404
from .store import ArtifactStore


def _file_or_404(path: str | pathlib.Path | None, media_type: str) -> FileResponse:
    if path is None or not pathlib.Path(path).is_file():
        raise ApiProblem(404, "not ready")
    return FileResponse(path, media_type=media_type)


def _jpeg_sensor_size(data: bytes) -> tuple[int, int]:
    """The stored sensor pixel dimensions declared by the frame's own header."""
    with PILImage.open(io.BytesIO(data)) as opened:
        width, height = opened.size
    return width, height


def _frame_entry(store: ArtifactStore, scan_id: uuid.UUID, artifact: Artifact) -> FrameEntry | None:
    """One listing entry, or None when the stored bytes are not a readable image."""
    try:
        width, height = _jpeg_sensor_size(store.artifact_path(scan_id, artifact.id).read_bytes())
    except (OSError, ValueError):
        return None
    return FrameEntry(
        frame_id=artifact.id,
        width=width,
        height=height,
        image_url=f"/api/scans/{scan_id}/frames/{artifact.id}",
    )


def scene_glb_response(database: Database, scan_id: uuid.UUID, revision: int | None) -> Response:
    """The exported geometry, and whether a newer export is still being made.

    The pending header goes out either way: a viewer that gets a 404 still
    needs to know the difference between nothing to show and not yet.
    """
    with database.connect() as connection:
        scan_or_404(connection, scan_id)
        found = revisions_repo.display_geometry(connection, scan_id, revision)
        pending = jobs_repo.display_pending(connection, scan_id)
    response = _file_or_404(found[0], "model/gltf-binary") if found else Response(status_code=404)
    if found:
        response.headers["X-Exported-Revision"] = str(found[1])
    response.headers["X-Display-Pending"] = str(pending).lower()
    return response


def _crop_file(store: ArtifactStore, scan_id: uuid.UUID, crop_id: str) -> tuple[pathlib.Path, str]:
    """The stored crop named by this id, and what it is.

    The id is a caller's string, so it is checked twice: once for the obvious
    traversal spellings, and again by resolving the result and requiring it to
    still sit under the scan's own crops directory. A symlink cannot carry it
    out of there either, because the comparison happens after resolution.
    """
    if "/" in crop_id or "\\" in crop_id or ".." in crop_id:
        raise ApiProblem(400, "invalid crop id")
    filename = crop_id if crop_id.endswith((".jpg", ".png")) else f"{crop_id}.jpg"
    crops_dir = (store.scan_dir(scan_id) / "crops").resolve()
    crop_path = (crops_dir / filename).resolve()
    if not str(crop_path).startswith(str(crops_dir)):
        raise ApiProblem(400, "invalid crop path")
    if not crop_path.is_file():
        raise ApiProblem(404, "crop not found")
    return crop_path, "image/png" if filename.endswith(".png") else "image/jpeg"


def _frame_listing(store: ArtifactStore, scan_id: uuid.UUID, stored: list[Artifact]) -> FrameListing:
    """Split the stored frames into the ones that open and the ones that do not.

    An artifact whose bytes will not read as an image is reported by id rather
    than failing the listing, so one bad frame does not hide the rest.
    """
    entries: list[FrameEntry] = []
    unreadable: list[str] = []
    for artifact in stored:
        entry = _frame_entry(store, scan_id, artifact)
        if entry is None:
            unreadable.append(artifact.id)
        else:
            entries.append(entry)
    return FrameListing(frames=entries, unreadable=unreadable)


def render_response(
    store: ArtifactStore, database: Database, scan_id: uuid.UUID, finding_id: uuid.UUID
) -> FileResponse:
    with database.connect() as connection:
        revision = revisions_repo.get_revision(connection, scan_id)
    if revision is None:
        raise ApiProblem(404, "not ready")
    directory = store.scan_dir(scan_id) / "revisions"
    matches = sorted(directory.glob(f"*/renders/{finding_id}.png"), key=lambda path: int(path.parent.parent.name))
    return _file_or_404(matches[-1] if matches else None, "image/png")


def install_file_routes(app: FastAPI, database: Database, store: ArtifactStore) -> None:
    @app.head("/api/scans/{scan_id}/scene.glb")
    @app.get("/api/scans/{scan_id}/scene.glb")
    def scene_glb(scan_id: uuid.UUID, revision: int | None = None) -> Response:
        return scene_glb_response(database, scan_id, revision)

    @app.get("/api/scans/{scan_id}/lidar-mesh")
    def lidar_mesh(scan_id: uuid.UUID) -> FileResponse:
        with database.connect() as connection:
            scan_or_404(connection, scan_id)
            artifact = repo.artifact_of_kind(connection, scan_id, "lidar_mesh")
        path = store.artifact_path(scan_id, artifact.id) if artifact else None
        return _file_or_404(path, "application/json")

    @app.get("/api/scans/{scan_id}/renders/{finding_id}.png")
    def render(scan_id: uuid.UUID, finding_id: uuid.UUID) -> FileResponse:
        return render_response(store, database, scan_id, finding_id)

    @app.get("/api/scans/{scan_id}/crops/{crop_id}")
    def get_crop(scan_id: uuid.UUID, crop_id: str) -> FileResponse:
        with database.connect() as connection:
            scan_or_404(connection, scan_id)
        crop_path, media_type = _crop_file(store, scan_id, crop_id)
        return FileResponse(crop_path, media_type=media_type)

    @app.get("/api/scans/{scan_id}/frames", response_model=FrameListing)
    def frames(scan_id: uuid.UUID) -> FrameListing:
        """The stored source-resolution frames a photo review can open.

        Authenticated by the same ownership middleware as every other scan
        route. Each entry names one ACTUAL stored frame artifact; a scan with
        no frames returns an empty list, never invented identities. A stored
        artifact whose bytes are not a readable image is reported by id under
        `unreadable` instead of failing the whole listing.
        """
        with database.connect() as connection:
            scan_or_404(connection, scan_id)
            stored = repo.artifacts_of_kind(connection, scan_id, "frames")
        return _frame_listing(store, scan_id, stored)

    @app.get("/api/scans/{scan_id}/frames/{frame_id}")
    def frame_bytes(scan_id: uuid.UUID, frame_id: str) -> Response:
        """The original bytes of one stored frame, never a downscaled copy."""
        if not re.fullmatch(FRAME_ID_PATTERN, frame_id):
            raise ApiProblem(400, "invalid frame id")
        with database.connect() as connection:
            scan_or_404(connection, scan_id)
            artifact = repo.find_artifact(connection, scan_id, frame_id)
        if artifact is None or artifact.kind != "frames":
            raise ApiProblem(404, "frame not found")
        return Response(
            content=store.artifact_path(scan_id, artifact.id).read_bytes(),
            media_type="image/jpeg",
        )
