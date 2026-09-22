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

GEOMETRY_REQUIRED_ARTIFACT_KINDS = ("room_json", "room_usdz")
"""What a measured room needs before geometry is usable. Upload alone is not a receipt."""

SEMANTIC_REQUIRED_ARTIFACT_KINDS = ("frames", "poses", "lidar_mesh")
"""What photo recognition needs: resolvable photos, their poses, and the measured mesh.

`photo_manifest` and `coverage` are validated when present but not required: a
scan can carry adequate calibrated stills without video or a coverage pass.
"""

SemanticState = Literal[
    "not_started", "blocked_incomplete_evidence", "queued", "running", "complete", "failed"
]

GeometryState = Literal["awaiting", "ready", "failed"]
EvidenceState = Literal["awaiting", "partial", "complete", "incomplete"]


class EvidenceBundle(BaseModel):
    """One immutable closure of the artifacts a scan has at a point in time.

    Version 1 is written when the scan is first completed. Evidence that arrives
    after the first closure becomes version 2, and so on; old bundles are kept.
    `semantic_processed_hash` records the manifest a semantic job actually
    consumed, which is how a changed bundle schedules exactly one new job.
    """

    version: int = 1
    manifest_hash: str
    artifact_ids: list[str] = []
    artifact_hashes: dict[str, str] = {}
    """Latest sha256 per artifact kind, so a changed bundle is detectable by hash."""
    complete: bool = False
    missing_required_kinds: list[str] = []
    reasons: list[str] = []
    created_at: datetime | None = None
    semantic_processed_hash: str | None = None
    """Null until a semantic job has consumed this exact manifest."""


class EvidenceStatus(BaseModel):
    """Where a scan is between raw upload and processed semantic evidence.

    The three states are separate on purpose: a room can have usable geometry
    while photo recognition is still blocked, and "ready" on the legacy Scan
    state must not be read as complete evidence.
    """

    scan_id: UUID
    geometry_state: GeometryState
    evidence_state: EvidenceState
    semantic_state: SemanticState
    present_kinds: list[str] = []
    missing_geometry_kinds: list[str] = []
    missing_semantic_kinds: list[str] = []
    reasons: list[str] = []
    bundle_version: int = 0
    manifest_hash: str | None = None
    complete_evidence: bool = False
    semantic_job_pending: bool = False
    latest_bundle: EvidenceBundle | None = None


class CompleteRequest(BaseModel):
    """The optional body of POST /complete.

    A legacy client sends no body at all and keeps its existing behavior. A
    client that tracks its own uploads can declare what it believes it uploaded;
    the server still decides readiness from stored bytes.
    """

    declared_complete: bool = True
    manifest_hash: str | None = None
    client_version: str | None = None


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
