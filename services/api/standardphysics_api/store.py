"""Uploaded bytes on the local filesystem, addressed by validated IDs only.

A path is always built from a scan UUID and an artifact ID that passed
`ARTIFACT_ID`, never from anything else a client sends, and the resolved path
must stay under the root.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import tempfile
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

ARTIFACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class InvalidArtifactId(ValueError):
    pass


class ArtifactTooLarge(ValueError):
    pass


class ScanFull(ValueError):
    pass


@dataclass(frozen=True)
class ScanQuota:
    """How much one scan may hold, so a client can't fill the disk one allowed artifact at a time.

    The largest walk on file holds 3,526 artifacts and 2.4 GiB: about 3,500 photo
    frames of 600 KB, a 390 MB LiDAR mesh and the room files. The defaults leave
    room for a walk around three times that.
    """

    max_artifacts: int = 10_000
    max_bytes: int = 8 * 1024 * 1024 * 1024

    def admit(self, held: int, held_bytes: int, incoming_bytes: int) -> None:
        if held + 1 > self.max_artifacts:
            raise ScanFull(f"this scan already holds {self.max_artifacts} artifacts")
        if held_bytes + incoming_bytes > self.max_bytes:
            raise ScanFull(f"this scan would hold more than {self.max_bytes} bytes")


@dataclass(frozen=True)
class StagedUpload:
    temp_path: pathlib.Path
    sha256: str
    bytes: int


class ArtifactStore:
    def __init__(self, root: pathlib.Path, max_bytes: int, quota: ScanQuota = ScanQuota()):
        self.root = root.resolve()
        self.max_bytes = max_bytes
        self.quota = quota

    def artifact_path(self, scan_id: uuid.UUID, artifact_id: str) -> pathlib.Path:
        if not ARTIFACT_ID.fullmatch(artifact_id):
            raise InvalidArtifactId(artifact_id)
        path = (self.root / "scans" / str(scan_id) / "artifacts" / artifact_id).resolve()
        if not path.is_relative_to(self.root):
            raise InvalidArtifactId(artifact_id)
        return path

    def scan_dir(self, scan_id: uuid.UUID) -> pathlib.Path:
        return self.root / "scans" / str(scan_id)

    def remove_scan(self, scan_id: uuid.UUID) -> None:
        """Delete everything stored for one scan.

        The path is built from the scan UUID alone and checked against the
        root, the same way a read is, so a delete can never walk out of the
        store.
        """
        import shutil

        target = self.scan_dir(scan_id)
        if not str(target).startswith(str(self.root)):
            raise InvalidArtifactId(str(scan_id))
        shutil.rmtree(target, ignore_errors=True)

    async def stage(self, scan_id: uuid.UUID, chunks: AsyncIterator[bytes]) -> StagedUpload:
        """Stream a body to a temp file beside its destination while hashing it."""
        directory = self.scan_dir(scan_id) / "artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        handle = tempfile.NamedTemporaryFile(dir=directory, prefix=".upload-", delete=False)
        try:
            with handle:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ArtifactTooLarge(size)
                    digest.update(chunk)
                    handle.write(chunk)
        except BaseException:
            pathlib.Path(handle.name).unlink(missing_ok=True)
            raise
        return StagedUpload(pathlib.Path(handle.name), digest.hexdigest(), size)

    @staticmethod
    def commit(staged: StagedUpload, destination: pathlib.Path) -> None:
        os.replace(staged.temp_path, destination)

    @staticmethod
    def discard(staged: StagedUpload) -> None:
        staged.temp_path.unlink(missing_ok=True)
