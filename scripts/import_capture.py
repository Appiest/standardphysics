"""Import one phone capture (a full capture directory) as its own scan.

Uploads the same artifacts the phone sends: room geometry, lidar mesh, camera
poses, every keyframe, and the photo manifest — the set the texture bake needs
to colour the surface. Prints the scan id when it is queued.

    TOKEN=<sp_session cookie> .venv/bin/python scripts/import_capture.py \
        --name "center" /tmp/sp-appdata/Captures/454B3661-...

The cookie is a signed-in owner's `sp_session` value.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import urllib.error
import urllib.request

DEFAULT_API = os.environ.get("SP_API", "http://127.0.0.1:8787")


def post(url: str, payload: dict, token: str) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Cookie": f"sp_session={token}"},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read() or b"{}")


def put(api: str, scan_id: str, artifact_id: str, kind: str, source: pathlib.Path, token: str) -> None:
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    request = urllib.request.Request(
        f"{api}/api/scans/{scan_id}/artifacts/{artifact_id}",
        data=data, method="PUT",
        headers={
            "Content-Type": "application/octet-stream",
            "X-Artifact-Kind": kind,
            "X-Checksum-SHA256": digest,
            "Cookie": f"sp_session={token}",
        },
    )
    urllib.request.urlopen(request).close()


def frame_entries(directory: pathlib.Path) -> list[tuple[str, str, str]]:
    manifest = json.loads((directory / "photo-manifest.json").read_text())
    return [(frame["frame_id"], frame["sha256"], frame["bytes"]) for frame in manifest["frames"]]


def capture_name(directory: pathlib.Path, fallback: str) -> str:
    capture = directory / "capture.json"
    if capture.exists():
        try:
            name = json.loads(capture.read_text()).get("name")
            if name:
                return name
        except (json.JSONDecodeError, OSError):
            pass
    return fallback


def import_capture(directory: pathlib.Path, name: str, api: str, token: str) -> str:
    created = post(f"{api}/api/scans", {"name": name, "device_model": "iPhone17,1", "duration_seconds": 0.0}, token)
    scan_id = created["id"]

    artifacts = [
        ("room-json", "room_json", "room.json"),
        ("room-usdz", "room_usdz", "room.usdz"),
        ("room-metadata", "room_metadata", "room.metadata.json"),
        ("lidar-mesh", "lidar_mesh", "lidar-mesh.json"),
        ("poses", "poses", "poses.json"),
        ("coverage", "coverage", "coverage.json"),
        ("photo-manifest", "photo_manifest", "photo-manifest.json"),
    ]
    for artifact_id, kind, filename in artifacts:
        source = directory / filename
        if source.exists():
            put(api, scan_id, artifact_id, kind, source, token)

    frames_dir = directory / "frames"
    for frame_id, sha, _ in frame_entries(directory):
        put(api, scan_id, frame_id, "frames", frames_dir / f"{frame_id.replace('-', '_')}.jpg", token)

    post(f"{api}/api/scans/{scan_id}/complete", {}, token)
    return scan_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+", type=pathlib.Path)
    parser.add_argument("--name", default=None)
    parser.add_argument("--api", default=DEFAULT_API)
    args = parser.parse_args()

    token = os.environ.get("TOKEN")
    if not token:
        raise SystemExit("set TOKEN to the signed-in owner's sp_session cookie value")

    for directory in args.directories:
        name = args.name or capture_name(directory, directory.name)
        try:
            scan_id = import_capture(directory, name, args.api, token)
        except urllib.error.HTTPError as error:
            print(f"{directory.name}: {error.code} {error.read().decode()[:200]}")
            return 1
        print(f"{name}: queued as {scan_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
