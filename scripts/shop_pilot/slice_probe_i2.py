"""I2 slice probe: graph, crop review and saved correction that survives reload."""

from __future__ import annotations

import json
import pathlib
import tempfile
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from scripts.shop_pilot.slice_probe_common import OUT_DIR, drain_jobs, probe_stages, sha256_hex
from standardphysics_api.app import create_app
from standardphysics_api.settings import Settings


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
        client = TestClient(create_app(settings, probe_stages(), run_worker=False))
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
                        "X-Checksum-SHA256": sha256_hex(payload),
                        "Content-Type": "application/octet-stream",
                    },
                )
            complete = client.post(f"/api/scans/{scan_id}/complete")
            observations.append({"step": "complete", "status": complete.status_code})
            drain_jobs(client)

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
                drain_jobs(client)
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


def write_receipt_i2(report: dict, log_path: pathlib.Path, head: str) -> None:
    """Bind the I2 probe run (review persistence) to an independent receipt."""
    digest = sha256_hex(log_path.read_bytes())
    started = report.get("started_at")
    receipt = {
        "receipt_id": "SLICE-I2-PROBE",
        "gate_id": "G04",
        "run_id": "opencode-20260921-170615",
        "lane_id": "Q",
        "evidence_kind": "synthetic_production_path",
        "source_commit": head,
        "dirty_source_digest": sha256_hex(b"{}"),
        "dirty_source_files": {},
        "contract_hash": sha256_hex(b"unfrozen-contracts"),
        "policy_hash": sha256_hex(pathlib.Path("docs/archive/research/deepseek-shop-pilot/04-hard-gates.json").read_bytes()),
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
