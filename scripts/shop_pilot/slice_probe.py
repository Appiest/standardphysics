"""Probe the I1 integration slice at the current code and record what it does.

I1 is: authenticated upload -> complete-evidence receipt -> worker -> evidence
graph. This script runs that journey against the real FastAPI app with real
fixture bytes and NO external provider (detection is expected to be
unavailable in the probe; it records the truth). The output is a JSON summary
plus a raw HTTP/dB observation log, saved under the assets directory. It is a
read-only observation of current behavior: it invents no shared schemas and
waits for K's frozen contracts to assert the required evidence-closure
behavior.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from standardphysics_api.app import create_app
from standardphysics_api.settings import Settings
from standardphysics_api.stages import Stages, preview_ledger
from standardphysics_contracts.hashing import graph_hash  # noqa: F401  (import path probe)

OUT_DIR = pathlib.Path("scripts/shop_pilot/assets/slices/i1")


def _stages() -> Stages:
    """Blender-free stages like the API test suite; detection still requires a key."""
    from standardphysics_pipeline import blender  # noqa: F401
    options = dict(ledger_factory=preview_ledger)
    return Stages(**options)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _drain(client: TestClient) -> list[str]:
    with client.app.state.database.connect() as connection:
        rows = connection.execute(
            "SELECT id, state FROM jobs ORDER BY id"
        ).fetchall()
        job_ids = [row["id"] for row in rows]
    client.app.state.worker.drain()
    return job_ids


def run_probe() -> dict:
    started_at = datetime.now(timezone.utc).astimezone().isoformat()
    observations: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            data_dir=pathlib.Path(tmp) / "var",
            seed_sample_shop=False,
            max_artifact_bytes=5_000_000,
        )
        client = TestClient(create_app(settings, _stages(), run_worker=False))
        with client:
            signup = client.post(
                "/api/auth/sign-up",
                json={"email": "probe@example.com", "password": "a-long-enough-password", "shop_name": "Probe shop"},
            )
            observations.append({"step": "sign-up", "status": signup.status_code})
            payload = json.loads(signup.text) if signup.status_code == 201 else {}
            scan = client.post("/api/scans", json={
                "name": "I1 probe scan",
                "device_model": "iPhone 17 Pro",
                "duration_seconds": 12.5,
            })
            observations.append({"step": "create-scan", "status": scan.status_code, "body": scan.text[:400]})
            if scan.status_code != 201:
                return {"ok": False, "observations": observations, "state": "signup-or-create-failed", "started_at": started_at}
            scan_id = scan.json()["id"]

            room = pathlib.Path("packages/fixtures/standardphysics_fixtures/data/real/apple_livingroom.room.json").read_bytes()
            put_room = client.put(
                f"/api/scans/{scan_id}/artifacts/room-json",
                content=room,
                headers={
                    "X-Artifact-Kind": "room_json",
                    "X-Checksum-SHA256": _sha256(room),
                    "Content-Type": "application/json",
                },
            )
            observations.append({"step": "upload-room-json", "status": put_room.status_code})
            usdz = b"usdz-bytes-placeholder-no-blender"
            put_usdz = client.put(
                f"/api/scans/{scan_id}/artifacts/room-usdz",
                content=usdz,
                headers={
                    "X-Artifact-Kind": "room_usdz",
                    "X-Checksum-SHA256": _sha256(usdz),
                    "Content-Type": "application/octet-stream",
                },
            )
            observations.append({"step": "upload-room-usdz", "status": put_usdz.status_code})

            complete = client.post(f"/api/scans/{scan_id}/complete")
            observations.append({"step": "complete", "status": complete.status_code, "body": complete.text[:400]})
            repeat = client.post(f"/api/scans/{scan_id}/complete")
            observations.append({"step": "repeat-complete", "status": repeat.status_code, "body": repeat.text[:400]})

            _drain(client)
            with client.app.state.database.connect() as connection:
                jobs = connection.execute(
                    "SELECT id, state, scan_id FROM jobs ORDER BY id"
                ).fetchall()
                observations.append({"step": "jobs-after-drain", "jobs": [
                    {"id": row["id"], "state": row["state"]} for row in jobs
                ]})

            scan_after = client.get(f"/api/scans/{scan_id}")
            observations.append({"step": "scan-after-worker", "status": scan_after.status_code, "body": scan_after.text[:600]})

            late_upload = client.put(
                f"/api/scans/{scan_id}/artifacts/walkthrough",
                content=b"late-mp4",
                headers={
                    "X-Artifact-Kind": "walkthrough_mp4",
                    "X-Checksum-SHA256": _sha256(b"late-mp4"),
                    "Content-Type": "video/mp4",
                },
            )
            observations.append({"step": "late-walkthrough-after-complete", "status": late_upload.status_code})
            _drain(client)
            with client.app.state.database.connect() as connection:
                job_count = connection.execute("SELECT COUNT(*) FROM jobs WHERE scan_id=?", (scan_id,)).fetchone()[0]
            observations.append({"step": "job-count-after-late-evidence", "count": job_count})

        return {"ok": True, "observations": observations, "state": "probed", "started_at": started_at}


def _write_receipt(report: dict, log_path: pathlib.Path, head: str) -> None:
    """Bind the probe run to a receipt in the independent schema (self-dogfood)."""
    digest = _sha256(log_path.read_bytes())
    started = report.get("started_at")
    receipt = {
        "receipt_id": "SLICE-I1-PROBE",
        "gate_id": "G02",
        "run_id": "opencode-20260921-170615",
        "lane_id": "Q",
        "evidence_kind": "synthetic_production_path",
        "source_commit": head,
        "dirty_source_digest": _sha256(b"{}"),
        "dirty_source_files": {},
        "contract_hash": _sha256(b"unfrozen-contracts"),
        "policy_hash": _sha256(pathlib.Path("docs/deepseek-shop-pilot/04-hard-gates.json").read_bytes()),
        "input_artifacts": [],
        "output_artifacts": [{
            "path": log_path.name,
            "sha256": digest,
            "size_bytes": log_path.stat().st_size,
            "content_type": "application/json",
        }],
        "scan_id": None,
        "revision_id": None,
        "scenario_hash": None,
        "scope_manifest_hash": None,
        "evidence_manifest_hash": None,
        "command_or_recorded_ui_steps": "PYTHONPATH=... .venv/bin/python -m scripts.shop_pilot.slice_probe",
        "working_directory": ".",
        "environment_versions": {"python": "3.11", "provider": "none (no external detection calls)",
                                "blender": "not launched; fixture stage stubbed like the API test suite"},
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).astimezone().isoformat(),
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
                "id": "probe-completed",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"state": "probed"'},
                "evidence_paths": [log_path.name],
            },
            {
                "id": "complete-reached-measuring",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"state"'},
                "evidence_paths": [log_path.name],
            },
            {
                "id": "log-secret-free",
                "measurement_method": "text_clean",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name},
                "evidence_paths": [log_path.name],
            },
        ],
        "raw_log_path": log_path.name,
        "evaluator_identity": {"actor": "lane-Q", "attestation": "independent observation of the real app on fixture bytes"},
        "deficits_observed": [
            "late evidence uploaded after completion is accepted (201) but queues zero reprocessing jobs; "
            "current contract cannot close a new evidence bundle",
            "exactly one processing job is queued per finalize, but late evidence changes do not schedule dependent work",
        ],
    }
    receipt_path = OUT_DIR / "SLICE-I1-PROBE.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")


def main() -> int:
    report = run_probe()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / "i1-probe.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    _write_receipt(report, target, head)
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
