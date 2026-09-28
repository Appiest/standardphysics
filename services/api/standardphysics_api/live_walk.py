"""Photos uploaded while the walk is still going on, read as they land.

A phone that uploads each keyframe during the walk sends that frame's pose
with it in the `X-Frame-Pose` header: the same record it later writes into
poses.json. The pose is what lets the walk sampler decide, before poses.json
exists, whether the photo is worth reading. A frame uploaded without the header
is stored exactly as before and read by discovery at the end.
"""

from __future__ import annotations

import uuid

from pydantic import ValidationError
from standardphysics_contracts import PoseRecord
from standardphysics_pipeline.discovery.live import LiveReader

from .errors import ApiProblem
from .store import ArtifactStore
from .textures import room_detections_dir

FRAME_POSE_HEADER = "X-Frame-Pose"
MAX_FRAME_POSE_BYTES = 4096


def frame_pose(header: str | None, artifact_id: str, kind: str) -> PoseRecord | None:
    """The pose sent with a frame, checked before any bytes are read, or nothing when none was sent."""
    if header is None:
        return None
    if kind != "frames":
        raise ApiProblem(400, f"{FRAME_POSE_HEADER} only travels with a frame")
    if len(header) > MAX_FRAME_POSE_BYTES:
        raise ApiProblem(400, f"{FRAME_POSE_HEADER} is longer than {MAX_FRAME_POSE_BYTES} bytes")
    try:
        pose = PoseRecord.model_validate_json(header)
    except ValidationError:
        raise ApiProblem(400, "invalid frame pose") from None
    if pose.frame_id != artifact_id:
        raise ApiProblem(400, "the frame pose names a different frame")
    return pose


def read_during_walk(
    reader: LiveReader, store: ArtifactStore, scan_id: uuid.UUID, artifact_id: str, pose: PoseRecord | None
) -> None:
    """Hand a newly stored frame to the reader, into the cache discovery will consult."""
    if pose is not None:
        reader.offer(scan_id, pose, store.artifact_path(scan_id, artifact_id), room_detections_dir(store, scan_id))
