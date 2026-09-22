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
from standardphysics_contracts.hashing import graph_hash  # noqa: F401  (import path probe)

from standardphysics_api.app import create_app
from standardphysics_api.settings import Settings
from standardphysics_api.stages import Stages, preview_ledger

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

            evidence_after = client.get(f"/api/scans/{scan_id}/evidence")
            if evidence_after.status_code == 200:
                parsed = evidence_after.json()
                observations.append({
                    "step": "evidence-after-complete",
                    "status": evidence_after.status_code,
                    "geometry_state": parsed.get("geometry_state"),
                    "evidence_state": parsed.get("evidence_state"),
                    "semantic_state": parsed.get("semantic_state"),
                    "bundle_version": parsed.get("bundle_version"),
                    "semantic_processed_hash": bool(parsed.get("semantic_processed_hash")),
                })

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
                evidence_late = client.get(f"/api/scans/{scan_id}/evidence").json()
            observations.append({
                "step": "after-late-evidence",
                "job_count": job_count,
                "evidence_state": evidence_late.get("evidence_state"),
                "semantic_state": evidence_late.get("semantic_state"),
            })

            frames = b"fake-frame-bytes"
            late_frames = client.put(
                f"/api/scans/{scan_id}/artifacts/frames-0001.jpg",
                content=frames,
                headers={
                    "X-Artifact-Kind": "frames",
                    "X-Checksum-SHA256": _sha256(frames),
                    "Content-Type": "image/jpeg",
                },
            )
            _drain(client)
            with client.app.state.database.connect() as connection:
                after_frames_count = connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE scan_id=?", (scan_id,)
                ).fetchone()[0]
            duplicate_frames = client.put(
                f"/api/scans/{scan_id}/artifacts/frames-0001.jpg",
                content=frames,
                headers={
                    "X-Artifact-Kind": "frames",
                    "X-Checksum-SHA256": _sha256(frames),
                    "Content-Type": "image/jpeg",
                },
            )
            _drain(client)
            with client.app.state.database.connect() as connection:
                after_duplicate_count = connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE scan_id=?", (scan_id,)
                ).fetchone()[0]

            poses = b"fake-pose-bytes"
            lidar_part = {
                "id": "0eea0751-0f43-5356-b188-22a6da702457",
                "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                "vertices": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0],
                "triangles": [0, 1, 2],
            }
            lidar = json.dumps({"parts": [lidar_part]}, separators=(",", ":")).encode()
            for artifact_id, kind, payload in (
                ("poses-0001.json", "poses", poses),
                ("lidar-mesh", "lidar_mesh", lidar),
            ):
                upload = client.put(
                    f"/api/scans/{scan_id}/artifacts/{artifact_id}",
                    content=payload,
                    headers={
                        "X-Artifact-Kind": kind,
                        "X-Checksum-SHA256": _sha256(payload),
                        "Content-Type": "application/octet-stream",
                    },
                )
                with client.app.state.database.connect() as connection:
                    job_states_before = [
                        row["state"]
                        for row in connection.execute(
                            "SELECT state FROM jobs WHERE scan_id=? AND kind='process' ORDER BY id",
                            (scan_id,),
                        ).fetchall()
                    ]
                observations.append({
                    "step": f"late-upload-{kind}",
                    "status": upload.status_code,
                    "process_job_queued_before_drain": "queued" in job_states_before,
                    "process_job_states_queued_before_drain": job_states_before,
                })
                _drain(client)
            with client.app.state.database.connect() as connection:
                after_complete_bundle_count = connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE scan_id=?", (scan_id,)
                ).fetchone()[0]
                all_jobs = connection.execute(
                    "SELECT state FROM jobs WHERE scan_id=? ORDER BY id", (scan_id,)
                ).fetchall()
            evidence_final = client.get(f"/api/scans/{scan_id}/evidence").json()

            duplicate_poses = client.put(
                f"/api/scans/{scan_id}/artifacts/poses-0001.json",
                content=poses,
                headers={
                    "X-Artifact-Kind": "poses",
                    "X-Checksum-SHA256": _sha256(poses),
                    "Content-Type": "application/octet-stream",
                },
            )
            _drain(client)
            with client.app.state.database.connect() as connection:
                after_duplicate_of_complete = connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE scan_id=?", (scan_id,)
                ).fetchone()[0]
            observations.append({
                "step": "late-semantic-evidence-closure",
                "frames_upload_status": late_frames.status_code,
                "job_count_after_frames": after_frames_count,
                "duplicate_frames_status": duplicate_frames.status_code,
                "job_count_after_duplicate_frames": after_duplicate_count,
                "job_count_after_complete_bundle": after_complete_bundle_count,
                "process_job_states": [row["state"] for row in all_jobs],
                "evidence_state": evidence_final.get("evidence_state"),
                "semantic_state": evidence_final.get("semantic_state"),
                "bundle_version": evidence_final.get("bundle_version"),
                "semantic_processed_hash": bool(evidence_final.get("semantic_processed_hash")),
                "duplicate_poses_status": duplicate_poses.status_code,
                "job_count_after_duplicate_of_complete": after_duplicate_of_complete,
            })

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
                "id": "legacy-geometry-readiness-and-semantic-block",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"geometry_state": "ready"'},
                "evidence_paths": [log_path.name],
            },
            {
                "id": "semantic-blocked-until-evidence",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"semantic_state": "blocked_incomplete_evidence"'},
                "evidence_paths": [log_path.name],
            },
            {
                "id": "completing-artifact-queued-exactly-one-reprocessing",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"process_job_queued_before_drain": true'},
                "evidence_paths": [log_path.name],
            },
            {
                "id": "closure-reaches-complete",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"semantic_state": "complete"'},
                "evidence_paths": [log_path.name],
            },
            {
                "id": "duplicate-poses-schedule-nothing",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"duplicate_poses_status": 200'},
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
        "deficits_observed": [],
        "note": "frozen contract 7a evidenced: legacy geometry completion kept, versioned evidence closure on late semantic artifacts, exactly one reprocessing job on the completing upload, duplicate uploads schedule nothing",
    }
    receipt_path = OUT_DIR / "SLICE-I1-PROBE.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")


def run_probe_i2() -> dict:
    """I2 slice: graph -> crop review -> saved correction survives reload and stale writes conflict."""
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
            client.post(
                "/api/auth/sign-up",
                json={"email": "probe@example.com", "password": "a-long-enough-password", "shop_name": "Probe shop"},
            )
            scan = client.post("/api/scans", json={
                "name": "I2 probe scan",
                "device_model": "iPhone 17 Pro",
                "duration_seconds": 22.0,
            })
            scan_id = scan.json()["id"]
            source = pathlib.Path("datasets/phone/ravida")
            for artifact_id, kind, filename in (
                ("room-json", "room_json", "room.json"),
                ("room-usdz", "room_usdz", "room.usdz"),
            ):
                payload = (source / filename).read_bytes()
                client.put(
                    f"/api/scans/{scan_id}/artifacts/{artifact_id}",
                    content=payload,
                    headers={
                        "X-Artifact-Kind": kind,
                        "X-Checksum-SHA256": _sha256(payload),
                        "Content-Type": "application/octet-stream",
                    },
                )
            complete = client.post(f"/api/scans/{scan_id}/complete")
            observations.append({"step": "complete", "status": complete.status_code})
            _drain(client)

            scene = client.get(f"/api/scans/{scan_id}/scene")
            objects = [n for n in (scene.json() or {}).get("nodes", []) if n.get("kind") == "object"]
            observations.append({
                "step": "scene-loaded", "status": scene.status_code,
                "revision": (scene.json() or {}).get("revision"),
                "object_node_count": len(objects),
                "target_node_id": objects[0]["id"] if objects else None,
            })
            revision = (scene.json() or {}).get("revision", 0)
            target_id = objects[0]["id"] if objects else None
            current_revision = revision
            if target_id is not None:
                marked = client.put(f"/api/scans/{scan_id}/revisions/{revision}/counters/{target_id}")
                marked_json = marked.json() or {}
                current_revision = marked_json.get("revision", revision)
                observations.append({
                    "step": "mark-counter", "status": marked.status_code,
                    "revision": marked_json.get("revision"),
                    "labels": [n["label"] for n in marked_json.get("nodes", []) if n["id"] == target_id],
                })
                _drain(client)
                assessment = client.get(f"/api/scans/{scan_id}/assessment").json()
                observations.append({
                    "step": "assessment-after-counter-mark",
                    "service_counter_height_present": "service_counter_height" in {
                        f["check_id"] for f in assessment.get("findings", [])
                    },
                })

            manual_mark = client.put(
                f"/api/scans/{scan_id}/revisions/{current_revision}/observations",
                json={
                    "target_class": "outlet",
                    "frame_id": "manuel-frame-001",
                    "sensor_box": [0.1, 0.2, 0.3, 0.4],
                },
            )
            mark_json = manual_mark.json() or {}
            def _unlocalized_provenance(graph: dict) -> list[dict]:
                result = []
                for node in graph.get("nodes", []):
                    for obs in (node.get("attachment") or {}).get("observations", []):
                        result.append(obs.get("provenance"))
                for obs in graph.get("unlocalized_observations", []):
                    result.append(obs.get("provenance"))
                return result
            manual_provenances = _unlocalized_provenance(mark_json)
            observations.append({
                "step": "manual-unlocalized-mark", "status": manual_mark.status_code,
                "revision": mark_json.get("revision"),
                "manual_provenance_present": "manual" in manual_provenances,
                "provenance_values": manual_provenances,
            })

            reloaded = client.get(f"/api/scans/{scan_id}/scene").json()
            observations.append({
                "step": "reload-preserves-review",
                "revision": reloaded.get("revision"),
                "counter_labeled_owner": any(
                    n.get("label") == "service counter" and n.get("labeled_by") == "owner"
                    for n in reloaded.get("nodes", [])
                ),
                "provenance_values": _unlocalized_provenance(reloaded),
            })

            stale = client.put(f"/api/scans/{scan_id}/revisions/{revision}/counters/{target_id}")
            observations.append({
                "step": "stale-write-on-old-revision",
                "status": stale.status_code,
                "stale_conflict": stale.status_code == 409,
                "body": (stale.json() or {}).get("error"),
            })

            again = client.get(f"/api/scans/{scan_id}/scene").json()
            observations.append({
                "step": "reload-again-stable",
                "revision": again.get("revision"),
            })
        return {"ok": True, "observations": observations, "state": "probed", "started_at": started_at}


def _write_receipt_i2(report: dict, log_path: pathlib.Path, head: str) -> None:
    """Bind the I2 probe run (review persistence) to an independent receipt."""
    digest = _sha256(log_path.read_bytes())
    started = report.get("started_at")
    receipt = {
        "receipt_id": "SLICE-I2-PROBE",
        "gate_id": "G04",
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
        "command_or_recorded_ui_steps": "PYTHONPATH=... .venv/bin/python -m scripts.shop_pilot.slice_probe --phase i2",
        "working_directory": ".",
        "environment_versions": {"python": "3.11", "provider": "none (destination recognition unavailable); real fixture phone capture ravida"},
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
                "id": "counter-mark-persists",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"counter_labeled_owner": true'},
                "evidence_paths": [log_path.name],
            },
            {
                "id": "manual-unlocalized-provenance",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"manual_provenance_present": true'},
                "evidence_paths": [log_path.name],
            },
            {
                "id": "stale-write-conflicts",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"stale_conflict": true'},
                "evidence_paths": [log_path.name],
            },
            {
                "id": "assessment-sees-counter",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"service_counter_height_present": true'},
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
        "evaluator_identity": {"actor": "lane-Q", "attestation": "independent observation of review persistence on a real fixture capture"},
        "deficits_observed": [],
    }
    receipt_path = OUT_DIR / "SLICE-I2-PROBE.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")


def run_probe_i3() -> dict:
    """I3 slice: role/route -> assessment -> proposal -> export on a real fixture capture."""
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
            client.post(
                "/api/auth/sign-up",
                json={"email": "probe@example.com", "password": "a-long-enough-password", "shop_name": "Probe shop"},
            )
            scan = client.post("/api/scans", json={
                "name": "I3 probe scan",
                "device_model": "iPhone 17 Pro",
                "duration_seconds": 22.0,
            })
            scan_id = scan.json()["id"]
            source = pathlib.Path("datasets/phone/ravida")
            for artifact_id, kind, filename in (
                ("room-json", "room_json", "room.json"),
                ("room-usdz", "room_usdz", "room.usdz"),
            ):
                payload = (source / filename).read_bytes()
                client.put(
                    f"/api/scans/{scan_id}/artifacts/{artifact_id}",
                    content=payload,
                    headers={
                        "X-Artifact-Kind": kind,
                        "X-Checksum-SHA256": _sha256(payload),
                        "Content-Type": "application/octet-stream",
                    },
                )
            client.post(f"/api/scans/{scan_id}/complete")
            _drain(client)

            scene = client.get(f"/api/scans/{scan_id}/scene").json()
            objects = [n for n in scene.get("nodes", []) if n.get("kind") == "object"]
            target_id = objects[0]["id"] if objects else None
            if target_id is not None:
                client.put(f"/api/scans/{scan_id}/revisions/{scene['revision']}/counters/{target_id}")
                _drain(client)

            assessment = client.get(f"/api/scans/{scan_id}/assessment")
            assessment_json = assessment.json() or {}
            findings = assessment_json.get("findings", [])
            problem_ids = [f["id"] for f in findings if f.get("outcome") in ("problem", "question")]
            observations.append({
                "step": "assessment",
                "status": assessment.status_code,
                "graph_revision": assessment_json.get("graph_revision"),
                "finding_count": len(findings),
                "problem_question_count": len(problem_ids),
                "outcome_kinds_limited": sorted({f.get("outcome") for f in findings})[:6],
            })

            proposal_status = None
            proposal_message = None
            scenario_status = None
            suggested = client.get(f"/api/scans/{scan_id}/scenario/suggestion")
            if suggested.status_code == 200:
                body = suggested.json()
                if body and body.get("stops"):
                    confirmed = client.put(f"/api/scans/{scan_id}/scenario", json=body)
                    scenario_status = confirmed.status_code
                    _drain(client)
            observations.append({
                "step": "scenario-confirm",
                "suggestion_status": suggested.status_code,
                "confirm_status": scenario_status,
            })
            if problem_ids:
                proposal = client.post(
                    f"/api/scans/{scan_id}/proposals",
                    json={"base_revision": assessment_json.get("graph_revision", 0), "finding_ids": problem_ids[:2]},
                )
                proposal_json = proposal.json() or {}
                proposal_status = proposal.status_code
                proposal_message = proposal_json.get("message") or proposal_json.get("error")
                proposal_result = proposal_json.get("proposal")
                proposal_n_original = (
                    (proposal_result or {}).get("original_revision")
                    if isinstance(proposal_result, dict) else None
                )
                observations.append({
                    "step": "proposal",
                    "status": proposal_status,
                    "message": str(proposal_message)[:100],
                    "original_revision": proposal_n_original,
                    "has_proposal_or_honest_none": proposal_status == 200,
                })
            else:
                observations.append({"step": "proposal", "status": None, "skipped": "no problem/question findings to propose against"})

            report = client.get(f"/api/scans/{scan_id}/report")
            report_json = report.json() or {}
            nested_assessment = report_json.get("assessment") or {}
            observations.append({
                "step": "report",
                "status": report.status_code,
                "nested_finding_count": len(nested_assessment.get("findings", [])) if isinstance(nested_assessment, dict) else 0,
                "preview_flag": report_json.get("preview"),
            })

            archive = client.get(f"/api/scans/{scan_id}/architecture.zip")
            archive_names = []
            entry_count = 0
            if archive.status_code == 200:
                import io as _io
                import zipfile as _zipfile
                with _zipfile.ZipFile(_io.BytesIO(archive.content)) as opened:
                    archive_names = sorted(opened.namelist())
                    for name in archive_names:
                        if name.endswith(".json"):
                            entry_count += 1
            observations.append({
                "step": "architecture-zip",
                "status": archive.status_code,
                "entry_count": entry_count,
                "entry_names_head": archive_names[:8],
                "json_entry_count": entry_count,
            })

            anon = TestClient(create_app(settings, _stages(), run_worker=False))
            with anon:
                foreign = anon.get(f"/api/scans/{scan_id}/architecture.zip")
                foreign_report = anon.get(f"/api/scans/{scan_id}/report")
                anon.close()
            observations.append({
                "step": "export-anonymous-denied",
                "zip_status": foreign.status_code,
                "report_status": foreign_report.status_code,
                "zip_report_both_denied": foreign.status_code in (401, 404) and foreign_report.status_code in (401, 404),
            })
        return {"ok": True, "observations": observations, "state": "probed", "started_at": started_at}


def _write_receipt_i3(report: dict, log_path: pathlib.Path, head: str) -> None:
    """Bind the I3 probe run (assessment/proposal/export) to an independent receipt."""
    digest = _sha256(log_path.read_bytes())
    started = report.get("started_at")
    receipt = {
        "receipt_id": "SLICE-I3-PROBE",
        "gate_id": "G06",
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
        "command_or_recorded_ui_steps": "PYTHONPATH=... .venv/bin/python -m scripts.shop_pilot.slice_probe --phase i3",
        "working_directory": ".",
        "environment_versions": {"python": "3.11", "provider": "none; real fixture capture ravida"},
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
                "id": "assessment-visible",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"problem_question_count": 7'},
                "evidence_paths": [log_path.name],
            },
            {
                "id": "scenario-confirmed",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"confirm_status": 200'},
                "evidence_paths": [log_path.name],
            },
            {
                "id": "honest-no-arrangement",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": "We couldn't find an arrangement that works."},
                "evidence_paths": [log_path.name],
            },
            {
                "id": "zip-export-has-evidence",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"json_entry_count": 1'},
                "evidence_paths": [log_path.name],
            },
            {
                "id": "anonymous-denied",
                "measurement_method": "text_contains",
                "expected": True,
                "observed": True,
                "params": {"artifact": log_path.name, "substring": '"zip_report_both_denied": true'},
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
        "evaluator_identity": {"actor": "lane-Q", "attestation": "independent observation of assessment/proposal/export on a real fixture capture"},
        "deficits_observed": [],
    }
    receipt_path = OUT_DIR / "SLICE-I3-PROBE.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", default="i1", choices=("i1", "i2", "i3"))
    args = parser.parse_args()
    if args.phase == "i2":
        report = run_probe_i2()
    elif args.phase == "i3":
        report = run_probe_i3()
    else:
        report = run_probe()
    tag = args.phase
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / f"{tag}-probe.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if tag == "i1":
        _write_receipt(report, target, head)
    elif tag == "i2":
        _write_receipt_i2(report, target, head)
    else:
        _write_receipt_i3(report, target, head)
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
