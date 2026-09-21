from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

ArtifactKind = Literal[
    "room_usdz", "room_json", "room_metadata", "walkthrough_mp4",
    "frames", "poses", "coverage", "lidar_mesh", "photo_manifest",
]

ScanState = Literal["uploading", "measuring", "checking", "ready", "failed"]


class Artifact(BaseModel):
    id: str
    kind: ArtifactKind
    sha256: str
    bytes: int
    stored_path: str | None = None


class SurfaceCoverage(BaseModel):
    node_id: UUID
    observed_fraction: float
    viewpoint_count: int

    @property
    def done(self) -> bool:
        return self.observed_fraction >= 0.70 and self.viewpoint_count >= 2


class Scan(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    device_model: str
    duration_seconds: float
    state: ScanState = "uploading"
    artifacts: list[Artifact] = []
    coverage: list[SurfaceCoverage] = []
    content_hash: str | None = None
