from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts import compress_moffett_splat as compress


def _source_bytes() -> bytes:
    return (
        b"ply\n"
        b"format ascii 1.0\n"
        b"comment people mask is provenance-sensitive\n"
        b"element vertex 0\n"
        b"end_header\n"
        b"payload\n"
    )


def _write_converter(path: Path, *, source_to_mutate: Path | None = None, fail: bool = False) -> None:
    mutation = ""
    if source_to_mutate is not None:
        mutation = f"pathlib.Path({str(source_to_mutate)!r}).write_bytes(b'mutated after normalization\\n')"
    exit_code = 7 if fail else 0
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import pathlib\n"
        "import sys\n"
        "input_path = pathlib.Path(sys.argv[1])\n"
        "output_path = pathlib.Path(sys.argv[2])\n"
        "output_path.write_bytes(b'spz:' + input_path.read_bytes())\n"
        f"{mutation}\n"
        "print(json.dumps({'splats': 1, 'sh_degree': 0, 'maximum_coordinate_error_m': 0.0, "
        "'output_bytes': output_path.stat().st_size}))\n"
        f"raise SystemExit({exit_code})\n"
    )
    path.chmod(0o755)


def test_source_digest_is_for_bytes_compressed_and_converter_is_recorded(tmp_path: Path) -> None:
    source = tmp_path / "source.ply"
    original = _source_bytes()
    source.write_bytes(original)
    converter = tmp_path / "converter"
    _write_converter(converter, source_to_mutate=source)
    output = tmp_path / "scene.spz"

    compress.main([str(source), str(output), "--converter", str(converter)])

    report = json.loads(output.with_suffix(".provenance.json").read_text())
    normalized = original.replace(b"comment people mask is provenance-sensitive\n", b"")
    assert report["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert report["normalized_source_sha256"] == hashlib.sha256(normalized).hexdigest()
    assert report["converter_sha256"] == hashlib.sha256(converter.read_bytes()).hexdigest()
    assert source.read_bytes() != original
    assert output.is_file()


def test_converter_failure_leaves_no_published_pair(tmp_path: Path) -> None:
    source = tmp_path / "source.ply"
    source.write_bytes(_source_bytes())
    converter = tmp_path / "converter"
    _write_converter(converter, fail=True)
    output = tmp_path / "scene.spz"

    with pytest.raises(subprocess.CalledProcessError):
        compress.main([str(source), str(output), "--converter", str(converter)])

    assert not output.exists()
    assert not output.with_suffix(".provenance.json").exists()


def test_publication_failure_removes_the_other_final_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    staged_output = tmp_path / "staged.spz"
    staged_provenance = tmp_path / "staged.provenance.json"
    output = tmp_path / "scene.spz"
    provenance = output.with_suffix(".provenance.json")
    staged_output.write_bytes(b"spz")
    staged_provenance.write_text("{}\n")
    original_hardlink_to = Path.hardlink_to

    def fail_for_output(self: Path, target: Path) -> None:
        if self == output:
            raise OSError("simulated output publication failure")
        original_hardlink_to(self, target)

    monkeypatch.setattr(Path, "hardlink_to", fail_for_output)
    with pytest.raises(OSError, match="simulated output publication failure"):
        compress._publish_pair(staged_output, staged_provenance, output, provenance)

    assert not output.exists()
    assert not provenance.exists()
