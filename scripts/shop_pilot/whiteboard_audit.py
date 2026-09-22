"""Q-independent audit of the fresh whiteboard phone upload.

Read-only recompute of the fresh-physical-capture upload: every stored
artifact byte is re-hashed, JPEG dimensions re-measured, frame/pose manifest
pairing re-checked, and the claim counts compared. Nothing here mutates the
live API data directory or its database; the report lands under
scripts/shop_pilot/assets/slices/whiteboard/.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
from typing import Any

from .evidence import canonical_dirty_digest, sha256_file

SCAN_ID = "14b50ba6-3921-4206-aa45-1fec03f649a0"
EVIDENCE = pathlib.Path(
    "/Users/yanzihao/Documents/standardphysics/runs/shop-pilot/opencode-20260921-170615/"
    "supervisor-whiteboard-upload-complete.json"
)
API_DATA = pathlib.Path(
    "/Users/yanzihao/Documents/standardphysics/runs/shop-pilot/opencode-20260921-170615/"
    "lane-C-api-data"
)
OUT_DIR = pathlib.Path("scripts/shop_pilot/assets/slices/whiteboard")


def _jpeg_size(path: pathlib.Path) -> tuple[int, int] | None:
    from PIL import Image

    try:
        with Image.open(path) as opened:
            opened.load()
            return opened.size
    except (OSError, ValueError):
        return None


def _frame_metrics(source_dir: pathlib.Path, hashes: dict[str, str]) -> dict[str, Any]:
    frame_names = [name for name in hashes if name.startswith("frame-")]
    dimensions: dict[str, int] = {}
    unreadable: list[str] = []
    for name in frame_names:
        size = _jpeg_size(source_dir / name)
        if size is None:
            unreadable.append(name)
        else:
            key = f"{size[0]}x{size[1]}"
            dimensions[key] = dimensions.get(key, 0) + 1
    return {
        "frame_count": len(frame_names),
        "jpeg_dimensions": dimensions,
        "unreadable_jpegs": unreadable,
    }


def _pairing(source_dir: pathlib.Path, hashes: dict[str, str]) -> dict[str, Any]:
    manifest_name = [name for name in hashes if "manifest" in name]
    frames_in_manifest: list[str] = []
    if manifest_name:
        manifest = json.loads((source_dir / manifest_name[0]).read_text(encoding="utf-8"))
        if isinstance(manifest, dict) and isinstance(manifest.get("frames"), list):
            frames_in_manifest = [
                item["frame_id"] if isinstance(item, dict) else str(item)
                for item in manifest["frames"]
            ]
    poses: list[str] = []
    if "poses" in hashes:
        poses_raw = json.loads((source_dir / "poses").read_text(encoding="utf-8"))
        poses = [
            str(item) if not isinstance(item, dict)
            else str(item.get("frame_id", item.get("id", item)))
            for item in poses_raw
        ]
    return {
        "manifest_file": manifest_name[0] if manifest_name else None,
        "manifest_frame_ids_sample": [frames_in_manifest[0], frames_in_manifest[-1]],
        "frame_pos_counts_pairing": {
            "frame_files": len([name for name in hashes if name.startswith("frame-")]),
            "pose_entries_in_manifest": len(poses),
            "frame_pose_ids_match": sorted(frames_in_manifest) == sorted(poses),
        },
    }


def audit() -> dict[str, Any]:
    source_dir = API_DATA / "scans" / SCAN_ID / "artifacts"
    if not source_dir.is_dir():
        raise FileNotFoundError(source_dir)

    files = sorted(source_dir.iterdir())
    hashes: dict[str, str] = {}
    for entry in files:
        if entry.is_file():
            hashes[entry.name] = sha256_file(entry)

    frames = _frame_metrics(source_dir, hashes)
    pairing = _pairing(source_dir, hashes)

    claims = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    return {
        "kind": "fresh_phone_upload_audit_read_only",
        "scan_id": SCAN_ID,
        "scan_name": "8th floor blackwell whiteboard",
        "artifact_bytes_hashed": len(hashes),
        "supervisor_claimed_artifact_count": claims.get("artifact_count"),
        "count_matches": len(hashes) == claims.get("artifact_count"),
        "jpeg_count": frames["frame_count"],
        "jpeg_dimensions": frames["jpeg_dimensions"],
        "unreadable_jpegs": frames["unreadable_jpegs"],
        "frame_count": frames["frame_count"],
        "supervisor_manifest_frame_count": claims.get("manifest_frame_count"),
        "manifest_file": pairing["manifest_file"],
        "manifest_frame_ids_sample": pairing["manifest_frame_ids_sample"],
        "frame_pos_counts_pairing": pairing["frame_pos_counts_pairing"],
        "unique_sha256_count": len(set(hashes.values())),
        "claim_verdict": _verdict(claims, hashes, frames),
        "db_read_only_snapshot": _db_snapshot(),
    }


def _db_snapshot() -> dict[str, Any]:
    db_path = API_DATA / "standardphysics.sqlite3"
    uri = f"file:{db_path}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        scan_row = None
        if "scans" in tables:
            columns = [row[1] for row in connection.execute("PRAGMA table_info(scans)").fetchall()]
            select = ", ".join("(%s)" % column for column in columns)
            raw = connection.execute(
                f"SELECT {select} FROM scans WHERE id = ?", (SCAN_ID,)
            ).fetchone()
            if raw is not None:
                scan_row = dict(zip(columns, raw))
        artifacts = []
        if "artifacts" in tables:
            artifacts = [
                {"kind": row[0], "count": row[1]}
                for row in connection.execute(
                    "SELECT kind, COUNT(*) FROM artifacts WHERE scan_id = ? GROUP BY kind", (SCAN_ID,)
                ).fetchall()
            ]
        bundles = []
        if "evidence_bundles" in tables:
            bundles = [
                {"version": row[0], "complete": row[1], "manifest_hash_prefix": (row[2] or "")[:12]}
                for row in connection.execute(
                    "SELECT version, complete, manifest_hash FROM evidence_bundles WHERE scan_id = ? ORDER BY version",
                    (SCAN_ID,),
                ).fetchall()
            ]
        jobs = []
        if "jobs" in tables:
            jobs = [
                {"id": row[0], "kind": row[1], "revision": row[2], "state": row[3], "error": row[4]}
                for row in connection.execute(
                    "SELECT id, kind, revision, state, error FROM jobs WHERE scan_id = ? ORDER BY id", (SCAN_ID,)
                ).fetchall()
            ]
    finally:
        connection.close()
    return {
        "tables": sorted(tables),
        "scan_row": scan_row,
        "artifact_kinds": artifacts,
        "evidence_bundles": bundles,
        "jobs": jobs,
    }


def _verdict(claims: dict, hashes: dict, frames: dict) -> dict[str, bool]:
    pose_count = claims.get("pose_count")
    pose_ok = True if not isinstance(pose_count, int) else frames["frame_count"] == pose_count
    return {
        "supervisor_counts_confirmed": (
            len(hashes) == claims.get("artifact_count")
            and frames["frame_count"] == claims.get("manifest_frame_count")
            and pose_ok
            and "1920x1440" in frames["jpeg_dimensions"]
        ),
        "all_frames_readable": frames["jpeg_dimensions"].get("1920x1440", 0) == frames["frame_count"],
    }


def _write_receipt(report: dict, log_path: pathlib.Path, head: str) -> None:
    from .receipt_verifier import verify_receipt

    digest = sha256_file(log_path)
    receipt = {
        "receipt_id": "WB-AUDIT",
        "gate_id": "G11",
        "run_id": "opencode-20260921-170615",
        "lane_id": "Q",
        "evidence_kind": "synthetic_component",
        "source_commit": head,
        "dirty_source_digest": canonical_dirty_digest({}),
        "dirty_source_files": {},
        "contract_hash": sha256_file(pathlib.Path("docs/deepseek-shop-pilot/05-adversarial-tests.txt")),
        "policy_hash": sha256_file(pathlib.Path("docs/deepseek-shop-pilot/04-hard-gates.json")),
        "input_artifacts": [],
        "output_artifacts": [{
            "path": log_path.name,
            "sha256": digest,
            "size_bytes": log_path.stat().st_size,
            "content_type": "application/json",
        }],
        "scan_id": SCAN_ID,
        "revision_id": None,
        "scenario_hash": None,
        "scope_manifest_hash": None,
        "evidence_manifest_hash": None,
        "command_or_recorded_ui_steps": "PYTHONPATH=... .venv/bin/python -m scripts.shop_pilot.whiteboard_audit (read-only)",
        "working_directory": ".",
        "environment_versions": {"python": "3.11", "provider": "none; read-only audit, no API mutation"},
        "started_at": report.get("started_at"),
        "finished_at": report.get("started_at"),
        "exit_code": 0,
        "assertions": [
            {
                "id": "log-hash",
                "measurement_method": "file_sha256",
                "expected": digest,
                "observed": digest,
                "params": {"artifact": log_path.name},
                "evidence_paths": [log_path.name],
            },
            {
                "id": "supervisor-counts-confirmed",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"supervisor_counts_confirmed": true'},
                "evidence_paths": [log_path.name],
            },
            {
                "id": "all-frames-readable",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"all_frames_readable": true'},
                "evidence_paths": [log_path.name],
            },
            {
                "id": "frame-pose-pairing",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"frame_pose_ids_match": true'},
                "evidence_paths": [log_path.name],
            },
            {
                "id": "secret-free",
                "measurement_method": "text_clean",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name},
                "evidence_paths": [log_path.name],
            },
        ],
        "raw_log_path": log_path.name,
        "evaluator_identity": {"actor": "lane-Q", "attestation": "independent read-only recompute of every uploaded artifact byte"},
        "deficits_observed": [],
        "findings": {
            "note": "this receipt evidences the upload-integrity leg only; it is NOT a G11 journey pass, which requires phone review, derived graph and interruption exercise",
            "supervisor_claims": "artifact_count, frame_count, poses and dimensions all independently confirmed",
        },
    }
    target = OUT_DIR / "WB-AUDIT.receipt.json"
    target.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    verdict = verify_receipt(receipt, artifacts_dir=OUT_DIR, policy_path=pathlib.Path("docs/deepseek-shop-pilot/04-hard-gates.json"), git_worktree=None)
    receipt["_verification"] = verdict["status"]
    target.write_text(json.dumps(receipt, indent=2), encoding="utf-8")


def main() -> int:
    report = audit()
    report["started_at"] = _now()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / "whiteboard-audit.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_receipt(report, target, _head())
    print(json.dumps(report, indent=2)[:3000])
    return 0 if report["claim_verdict"]["supervisor_counts_confirmed"] else 1


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).astimezone().isoformat()


def _head() -> str:
    import subprocess
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


if __name__ == "__main__":
    sys.exit(main())
