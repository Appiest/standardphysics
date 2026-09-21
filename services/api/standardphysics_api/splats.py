"""Serve revision-pinned Gaussian splat display assets."""

from __future__ import annotations

import json
import math
import pathlib
import re
import uuid
from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from standardphysics_contracts import graph_hash

from . import repository as repo
from .db import Database
from .errors import ApiProblem
from .store import ArtifactStore

_ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.(?:ply|spz)$")
_NO_CACHE = {"Cache-Control": "private, no-store"}
_RIGID_TOLERANCE = 1e-5


@dataclass(frozen=True)
class _SplatAsset:
    filename: str
    transform: list[float]
    path: pathlib.Path


@dataclass(frozen=True)
class _SplatManifest:
    revision: int
    graph_hash: str
    directory: pathlib.Path
    assets: tuple[_SplatAsset, ...]


def _no_splats() -> ApiProblem:
    return ApiProblem(404, "no splats for this revision")


def _splats_directory(store: ArtifactStore, scan_id: uuid.UUID, revision: int) -> pathlib.Path:
    if revision < 0:
        raise _no_splats()
    root = store.root.resolve()
    directory = (store.scan_dir(scan_id) / "revisions" / str(revision) / "splats").resolve()
    if not directory.is_relative_to(root):
        raise _no_splats()
    return directory


def _safe_child(directory: pathlib.Path, name: str) -> pathlib.Path:
    candidate = (directory / name).resolve()
    if not candidate.is_relative_to(directory):
        raise _no_splats()
    return candidate


def _rigid_transform(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 16:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        return None
    transform = [float(item) for item in value]
    if not all(math.isfinite(item) for item in transform):
        return None
    if any(
        not math.isclose(transform[index], expected, abs_tol=_RIGID_TOLERANCE)
        for index, expected in ((12, 0.0), (13, 0.0), (14, 0.0), (15, 1.0))
    ):
        return None

    rotation = [transform[row * 4:row * 4 + 3] for row in range(3)]
    if not _proper_rotation(rotation):
        return None
    return transform


def _proper_rotation(rotation: list[list[float]]) -> bool:
    for row in rotation:
        if not math.isclose(sum(component * component for component in row), 1.0, abs_tol=_RIGID_TOLERANCE):
            return False
    for left in range(3):
        for right in range(left):
            if not math.isclose(
                sum(rotation[left][axis] * rotation[right][axis] for axis in range(3)),
                0.0,
                abs_tol=_RIGID_TOLERANCE,
            ):
                return False
    determinant = (
        rotation[0][0] * (rotation[1][1] * rotation[2][2] - rotation[1][2] * rotation[2][1])
        - rotation[0][1] * (rotation[1][0] * rotation[2][2] - rotation[1][2] * rotation[2][0])
        + rotation[0][2] * (rotation[1][0] * rotation[2][1] - rotation[1][1] * rotation[2][0])
    )
    return math.isclose(determinant, 1.0, abs_tol=_RIGID_TOLERANCE)


def _saved_graph_hash(database: Database, scan_id: uuid.UUID, revision: int) -> str | None:
    with database.connect() as connection:
        row = repo.get_revision(connection, scan_id, revision)
        if row is None:
            return None
        try:
            computed = graph_hash(repo.graph_of(row))
        except (TypeError, ValueError):
            return None
        return computed if row["graph_hash"] == computed else None


def _manifest_assets(directory: pathlib.Path, entries: Any) -> tuple[_SplatAsset, ...]:
    if not isinstance(entries, list):
        raise _no_splats()

    assets: list[_SplatAsset] = []
    filenames: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str):
            raise _no_splats()
        filename = entry["filename"]
        if not _ASSET_NAME.fullmatch(filename) or filename in filenames:
            raise _no_splats()
        transform = _rigid_transform(entry.get("transform"))
        if transform is None:
            raise _no_splats()
        path = _safe_child(directory, filename)
        if not path.is_file():
            raise _no_splats()
        filenames.add(filename)
        assets.append(_SplatAsset(filename=filename, transform=transform, path=path))
    return tuple(assets)


def _load_manifest(
    database: Database, store: ArtifactStore, scan_id: uuid.UUID, revision: int
) -> _SplatManifest:
    directory = _splats_directory(store, scan_id, revision)
    try:
        source = _safe_child(directory, "manifest.json")
        if not source.is_file():
            raise _no_splats()
        raw = json.loads(source.read_text())
        if not isinstance(raw, dict) or type(raw.get("revision")) is not int:
            raise _no_splats()
        if raw["revision"] != revision or not isinstance(raw.get("graph_hash"), str):
            raise _no_splats()
        expected_hash = _saved_graph_hash(database, scan_id, revision)
        if expected_hash is None or raw["graph_hash"] != expected_hash:
            raise _no_splats()
        return _SplatManifest(
            revision=revision,
            graph_hash=raw["graph_hash"],
            directory=directory,
            assets=_manifest_assets(directory, raw.get("assets")),
        )
    except ApiProblem:
        raise
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        raise _no_splats() from None


def _asset_url(scan_id: uuid.UUID, revision: int, filename: str) -> str:
    return f"/api/scans/{scan_id}/splats/{quote(filename, safe='')}?revision={revision}"


def install_splat_routes(app: FastAPI, database: Database, store: ArtifactStore) -> None:
    @app.get("/api/scans/{scan_id}/splats")
    def manifest(scan_id: uuid.UUID, revision: Annotated[int, Query(ge=0)]) -> JSONResponse:
        found = _load_manifest(database, store, scan_id, revision)
        return JSONResponse(
            {
                "revision": found.revision,
                "graph_hash": found.graph_hash,
                "assets": [
                    {"url": _asset_url(scan_id, found.revision, asset.filename), "transform": asset.transform}
                    for asset in found.assets
                ],
                "display_only": True,
            },
            headers=_NO_CACHE,
        )

    @app.get("/api/scans/{scan_id}/splats/{filename}")
    def asset(
        scan_id: uuid.UUID,
        filename: str,
        revision: Annotated[int, Query(ge=0)],
    ) -> FileResponse:
        found = _load_manifest(database, store, scan_id, revision)
        selected = next((item for item in found.assets if item.filename == filename), None)
        if selected is None:
            raise _no_splats()
        return FileResponse(selected.path, media_type="application/octet-stream", headers=_NO_CACHE)
