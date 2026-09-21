"""Photo textures: what the phone sends so photos can be projected, and what the viewer reads back.

Textures are display only. A texture build never changes a measurement, a
finding, a scene revision or anything a check reads.
"""

from __future__ import annotations

import math
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .scene import SceneGraph

POSE_METADATA_VERSION = 2
FRAME_ID_PATTERN = r"^frame-[0-9]{4,}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"

PhotoOrientation = Literal["sensor"]
"""Pixels exactly as the camera delivered them, never rotated to the screen."""


class PoseRecord(BaseModel):
    """One keyframe in poses.json. Version 1 records lack the image metadata."""

    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    metadata_version: int = 1
    frame_id: str | None = Field(default=None, pattern=FRAME_ID_PATTERN)
    image: str
    timestamp: float
    transform: list[float] = Field(min_length=16, max_length=16)
    """ARKit camera-to-world, column-major, Y up, camera looking down local -Z."""
    intrinsics: list[float] = Field(min_length=9, max_length=9)
    """Column-major 3x3 in calibration pixels: fx at 0, fy at 4, cx at 6, cy at 7."""
    orientation: str = "unknown"
    image_width: int | None = Field(default=None, gt=0)
    image_height: int | None = Field(default=None, gt=0)
    calibration_width: int | None = Field(default=None, gt=0)
    calibration_height: int | None = Field(default=None, gt=0)
    image_orientation: PhotoOrientation | None = None

    @model_validator(mode="after")
    def valid_camera(self) -> PoseRecord:
        if self.intrinsics[0] <= 0 or self.intrinsics[4] <= 0:
            raise ValueError("camera focal lengths must be positive")
        if any(abs(self.transform[index]) > 1e-5 for index in (3, 7, 11)) or abs(self.transform[15] - 1) > 1e-5:
            raise ValueError("camera transform must be affine")
        columns = [self.transform[start:start + 3] for start in (0, 4, 8)]
        for index, column in enumerate(columns):
            for other_index, other in enumerate(columns):
                expected = 1.0 if index == other_index else 0.0
                if not math.isclose(sum(a * b for a, b in zip(column, other)), expected, abs_tol=0.01):
                    raise ValueError("camera transform must be rigid")
        return self

    @property
    def projectable(self) -> bool:
        return (
            self.metadata_version >= POSE_METADATA_VERSION
            and self.frame_id is not None
            and None not in (self.image_width, self.image_height, self.calibration_width, self.calibration_height)
            and self.image_orientation == "sensor"
        )


class PhotoManifestFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frame_id: str = Field(pattern=FRAME_ID_PATTERN)
    sha256: str = Field(pattern=SHA256_PATTERN)
    bytes: int = Field(gt=0)


class PhotoManifest(BaseModel):
    """Written by the phone after its photos upload. A build waits until every listed frame is stored."""

    model_config = ConfigDict(extra="forbid")

    manifest_version: Literal[1] = 1
    poses_sha256: str = Field(pattern=SHA256_PATTERN)
    frames: list[PhotoManifestFrame] = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def frame_ids_are_unique(self) -> PhotoManifest:
        if len({frame.frame_id for frame in self.frames}) != len(self.frames):
            raise ValueError("frame ids must be unique")
        return self


TextureState = Literal["needs_photos", "waiting_for_photos", "not_started", "queued", "running", "complete", "failed"]
"""needs_photos: this scan has no synchronized photos, so a new capture is needed.
waiting_for_photos: a manifest arrived but some listed photos have not.
not_started: photos are complete and nothing is built for this layout's shapes.
"""


class NodeTextureCoverage(BaseModel):
    node_id: UUID
    textured_fraction: float = Field(ge=0.0, le=1.0)
    """Share of the node's photographable surface that received photo color.

    Faces no camera ever turned toward are left out of the total. A wall has a
    back and a table has an underside, and neither is a shot the owner failed
    to take.
    """


class TextureCoverage(BaseModel):
    textured_fraction: float = Field(ge=0.0, le=1.0)
    nodes: list[NodeTextureCoverage]
    needs_another_view: list[UUID]
    """Nodes whose surfaces were mostly unseen, or where the scan and the model disagreed."""


class TextureBuild(BaseModel):
    build_id: str
    glb_url: str
    scan_glb_url: str | None = None
    """The scanned surface itself, in colour, when it could be painted.

    The boxes are what a check measures and what an owner drags. This is what
    the room looks like: the LiDAR mesh with every vertex given the colour of
    the photo that saw it best.
    """
    coverage_mask_urls: list[str]
    """One grayscale mask per atlas, in atlas order: white where photos reached."""
    bake_graph: SceneGraph
    """The layout the photos were projected onto: every node at its captured placement."""
    coverage: TextureCoverage
    frames_used: int
    seconds: float
    scan_glb_url: str | None = None
    """The room as it was scanned, painted from the photos, when the build produced one."""


class TextureRequest(BaseModel):
    revision: int | None = Field(default=None, exclude_if=lambda value: value is None)


class TextureStatus(BaseModel):
    scan_id: UUID
    revision: int
    state: TextureState
    build: TextureBuild | None
    """The build for this revision, or else the newest finished one whose unchanged shapes still apply."""
    exact: bool
    """Whether `build` was made for exactly this revision's shapes."""
    stale_node_ids: list[UUID]
    """Nodes whose shape changed since `build`; they render with plain materials."""
    error: str | None
    can_retry: bool
