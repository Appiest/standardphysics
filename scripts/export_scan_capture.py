"""Write a scan stored on this machine back out as the capture directory the phone uploaded.

A walk captured here can then be taken to another server through the phone's
own upload, `import_capture.py`, and that server processes and paints it the
way it would a fresh capture. Files are hard links into the store, so nothing
is copied.

    .venv/bin/python scripts/export_scan_capture.py --scan <scan id> --out /tmp/walk
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3

CAPTURE_FILES = {
    "room-json": "room.json",
    "room-usdz": "room.usdz",
    "room-metadata": "room.metadata.json",
    "lidar-mesh": "lidar-mesh.json",
    "poses": "poses.json",
    "coverage": "coverage.json",
    "photo-manifest": "photo-manifest.json",
}


def export(scans: pathlib.Path, database: pathlib.Path, scan_id: str, out: pathlib.Path) -> int:
    artifacts = scans / scan_id / "artifacts"
    out.mkdir(parents=True)
    (out / "frames").mkdir()
    for artifact_id, filename in CAPTURE_FILES.items():
        if (artifacts / artifact_id).is_file():
            os.link(artifacts / artifact_id, out / filename)
    manifest = json.loads((artifacts / "photo-manifest").read_text())
    for frame in manifest["frames"]:
        os.link(artifacts / frame["frame_id"], out / "frames" / f"{frame['frame_id'].replace('-', '_')}.jpg")
    with sqlite3.connect(database) as connection:
        name = connection.execute("SELECT name FROM scans WHERE id=?", (scan_id,)).fetchone()[0]
    (out / "capture.json").write_text(json.dumps({"name": name}))
    return len(manifest["frames"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", required=True)
    parser.add_argument("--out", required=True, type=pathlib.Path, help="a directory that does not exist yet")
    parser.add_argument("--scans", type=pathlib.Path, default=pathlib.Path("services/api/var/scans"))
    parser.add_argument("--db", type=pathlib.Path, default=pathlib.Path("services/api/var/standardphysics.sqlite3"))
    args = parser.parse_args()
    photos = export(args.scans, args.db, args.scan, args.out)
    print(f"{args.scan}: {photos} photos written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
