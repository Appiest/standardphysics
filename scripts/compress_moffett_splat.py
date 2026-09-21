"""Compress a Brush PLY with official SPZ v2.0.0; preserve originals and scene axes."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path


def _sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _occupied(path: Path) -> bool:
    """Treat dangling symlinks as existing output names too."""
    return path.exists() or path.is_symlink()


def _normalize_source(source_path: Path, normalized_path: Path) -> tuple[str, str]:
    """Copy the converter input while hashing both source and normalized bytes."""
    source_digest = hashlib.sha256()
    normalized_digest = hashlib.sha256()
    with source_path.open("rb") as source, normalized_path.open("wb") as normalized:
        while True:
            line = source.readline()
            if not line:
                raise ValueError("Missing PLY end_header")
            source_digest.update(line)
            if not line.startswith(b"comment "):
                normalized.write(line)
                normalized_digest.update(line)
            if line.strip() == b"end_header":
                break

        while chunk := source.read(1024 * 1024):
            source_digest.update(chunk)
            normalized.write(chunk)
            normalized_digest.update(chunk)
    return source_digest.hexdigest(), normalized_digest.hexdigest()


def _converter_report(stdout: str) -> dict:
    lines = stdout.strip().splitlines()
    if not lines:
        raise ValueError("converter produced no JSON report")
    report = json.loads(lines[-1])
    if not isinstance(report, dict):
        raise ValueError("converter report must be a JSON object")
    return report


def _publish_pair(
    staged_output: Path,
    staged_provenance: Path,
    output: Path,
    provenance: Path,
) -> None:
    """Publish two staged files without leaving a partial pair on errors.

    Two directory entries cannot be created in one filesystem operation.  The
    provenance sidecar is linked first, so an interrupted publication cannot
    expose an SPZ without its provenance; any in-process failure removes names
    created by this invocation.
    """
    published: list[Path] = []
    try:
        provenance.hardlink_to(staged_provenance)
        published.append(provenance)
        output.hardlink_to(staged_output)
        published.append(output)
    except BaseException:
        for path in reversed(published):
            with contextlib.suppress(OSError):
                path.unlink()
        raise


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--converter", type=Path, default=Path("runs/moffett/tools/compress_moffett_splat"))
    args = parser.parse_args(argv)

    source = args.source.absolute()
    output = args.output.absolute()
    provenance_path = output.with_suffix(".provenance.json")
    if _occupied(output):
        parser.error("output already exists")
    if _occupied(provenance_path):
        parser.error("provenance output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)

    converter = args.converter.expanduser().resolve(strict=True)
    if not converter.is_file():
        parser.error(f"converter is not a file: {converter}")
    converter_sha256 = _sha256(converter)

    with tempfile.TemporaryDirectory(dir=output.parent) as temp:
        normalized = Path(temp) / "source.ply"
        # The reference SPZ loader requires a header without PLY comment lines.
        # Only the header changes; all Gaussian records remain byte-for-byte intact.
        source_sha256, normalized_source_sha256 = _normalize_source(source, normalized)
        temporary_output = Path(temp) / "delivery.spz"
        result = subprocess.run(
            [str(converter), str(normalized), str(temporary_output)],
            check=True,
            capture_output=True,
            text=True,
        )
        report = _converter_report(result.stdout)
        if not temporary_output.is_file():
            raise ValueError("converter did not create an SPZ output")

        converter_sha256_after = _sha256(converter)
        if converter_sha256_after != converter_sha256:
            raise RuntimeError("converter changed while compression was running")

        report.update(
            source=str(source),
            output=str(output),
            source_sha256=source_sha256,
            normalized_source_sha256=normalized_source_sha256,
            converter=str(converter),
            converter_sha256=converter_sha256,
            coordinate_system="unchanged scene Z-up metres",
            note="Compression error is not physical measurement accuracy",
        )
        staged_provenance = Path(temp) / provenance_path.name
        staged_provenance.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        # Hard links create each final name atomically and refuse overwrite.
        # Publish provenance first so an SPZ is never intentionally exposed
        # without its sidecar; _publish_pair cleans up on an in-process error.
        _publish_pair(temporary_output, staged_provenance, output, provenance_path)

    print(json.dumps(report))


if __name__ == '__main__':
    main()
