"""Byte-level evidence primitives shared by the shop-pilot evaluators.

A digest only proves identity, not truth. These helpers exist so an evaluator
can always open the actual artifact, hash it, and compare against what a
receipt claims, instead of copying a hash string out of self-reported JSON.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any

SHA256_HEX_LEN = 64


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_sha256_hex(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != SHA256_HEX_LEN:
        return False
    return all(char in "0123456789abcdef" for char in value)


def canonical_dirty_digest(entries: dict[str, str]) -> str:
    """Fingerprint a dirty working tree as sorted (relative path, sha256) pairs."""
    payload = {str(key): str(value) for key, value in sorted(entries.items())}
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return sha256_bytes(encoded.encode("utf-8"))


def load_json(path: pathlib.Path) -> Any:
    with open(path, "rb") as handle:
        return json.load(handle)


class ArtifactError(ValueError):
    """Raised when an artifact record cannot be resolved to real bytes."""


def resolve_artifact(record: Any, base_dir: pathlib.Path) -> dict[str, Any]:
    """Resolve one artifact record to a verified path/sha256/size.

    Rejects the old G9 shape where ``artifact_paths_and_hashes`` mapped a name
    to prose like ``"100"``. Every entry must be an object with a real path.
    """
    if not isinstance(record, dict):
        raise ArtifactError(f"artifact record must be an object, got {type(record).__name__}")
    raw_path = record.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ArtifactError("artifact is missing a string 'path'")
    declared_digest = record.get("sha256")
    if not is_sha256_hex(declared_digest):
        raise ArtifactError(f"artifact {raw_path!r} has a missing or malformed sha256")

    candidate = pathlib.Path(raw_path)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    if not candidate.is_file():
        raise ArtifactError(f"artifact {raw_path!r} does not exist as a file")

    actual_size = candidate.stat().st_size
    declared_size = record.get("size_bytes")
    if declared_size is not None and declared_size != actual_size:
        raise ArtifactError(
            f"artifact {raw_path!r} size {actual_size} != declared {declared_size}"
        )

    actual_digest = sha256_file(candidate)
    if actual_digest != declared_digest:
        raise ArtifactError(
            f"artifact {raw_path!r} sha256 {actual_digest} != declared {declared_digest}"
        )

    return {
        "path": str(candidate),
        "relative_path": raw_path,
        "sha256": actual_digest,
        "size_bytes": actual_size,
        "content_type": record.get("content_type"),
    }
