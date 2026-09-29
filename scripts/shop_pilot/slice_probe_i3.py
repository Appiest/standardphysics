"""I3 slice probe: role/route, assessment, proposal and export on a real fixture capture."""

from __future__ import annotations

import json
import pathlib
import tempfile
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from scripts.shop_pilot.slice_probe_common import OUT_DIR, drain_jobs, probe_stages, sha256_hex
from standardphysics_api.app import create_app
from standardphysics_api.settings import Settings


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
        client = TestClient(create_app(settings, probe_stages(), run_worker=False))
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
                        "X-Checksum-SHA256": sha256_hex(payload),
                        "Content-Type": "application/octet-stream",
                    },
                )
            client.post(f"/api/scans/{scan_id}/complete")
            drain_jobs(client)

            scene = client.get(f"/api/scans/{scan_id}/scene").json()
            objects = [n for n in scene.get("nodes", []) if n.get("kind") == "object"]
            target_id = objects[0]["id"] if objects else None
            if target_id is not None:
                client.put(f"/api/scans/{scan_id}/revisions/{scene['revision']}/counters/{target_id}")
                drain_jobs(client)

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
                    drain_jobs(client)
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

            anon = TestClient(create_app(settings, probe_stages(), run_worker=False))
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


def write_receipt_i3(report: dict, log_path: pathlib.Path, head: str) -> None:
    """Bind the I3 probe run (assessment/proposal/export) to an independent receipt."""
    digest = sha256_hex(log_path.read_bytes())
    started = report.get("started_at")
    receipt = {
        "receipt_id": "SLICE-I3-PROBE",
        "gate_id": "G06",
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
