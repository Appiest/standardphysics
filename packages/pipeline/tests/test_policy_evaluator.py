"""Policy evaluator acceptance tests (E01-E06) at the real CLI entry point.

Each test builds a tiny policy fixture (same schema, own hash), runs
scripts/evaluate_candidate.py as a subprocess, and asserts the exit code and
the JSON verdict.  Thresholds always come from the fixture policy file; the
tests never import policy constants into Python.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[3]
CLI = REPO / "scripts/evaluate_candidate.py"

BASELINE_POLICY = REPO / "docs/archive/research/deepseek-lidar-experiment-policy-v2.json"

VIEW_IDS = [f"image-{n:06d}.jpg" for n in range(100, 108)]


def _mini_policy(tmp_path: Path, overrides: dict | None = None) -> Path:
    raw = json.loads(BASELINE_POLICY.read_text())
    raw["hard_non_numeric_gates"] = [
        "originals_and_previous_revisions_preserved",
        "frozen_input_split_evaluator_and_artifact_hashes_validate",
    ]
    raw["_policy_path"] = None
    raw.pop("_policy_path", None)
    if overrides:
        for key, value in overrides.items():
            if key.startswith("gate."):
                raw["hard_numeric_gates"][key[5:]] = value
            else:
                raw[key] = value
    pathlib_tmp = Path(tmp_path)
    pathlib_tmp.mkdir(parents=True, exist_ok=True)
    path = pathlib_tmp / "policy.json"
    path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n")
    return path


def _run_record(
    tmp_path: Path,
    *,
    policy_path: Path,
    views: list[dict],
    resources: dict | None = None,
    repeats: list[dict] | None = None,
    rois: dict | None = None,
    coverage: dict | None = None,
    viewer: dict | None = None,
    non_numeric: dict | None = None,
    hashes: dict | None = None,
    extra: dict | None = None,
) -> dict:
    resources = resources or {
        "cold_end_to_end_seconds": 120.0,
        "peak_footprint_bytes": 1_000_000_000,
        "compressed_spz_bytes": 10_000_000,
        "footprint_method": "phys_footprint",
        "includes_compressed_export": True,
    }
    repeats = repeats or [{"index": i} for i in range(3)]
    viewer = viewer or {
        "required": 3,
        "recorded": 3,
        "gpu_identity": "metal",
        "completed_frame_samples": 300,
        "worst_session_p95_ms": 16.0,
    }
    coverage = coverage or {"alpha_available": True, "uncovered_fraction": 0.05}
    non_numeric = non_numeric if non_numeric is not None else {
        "originals_and_previous_revisions_preserved": {
            "passed": True, "evidence_paths": [str(tmp_path / "evidence.txt")],
        },
        "frozen_input_split_evaluator_and_artifact_hashes_validate": {
            "passed": True, "evidence_paths": [str(tmp_path / "evidence.txt")],
        },
    }
    record = {
        "expected_views": VIEW_IDS,
        "views": views,
        "resources": resources,
        "repeats": repeats,
        "rois": rois or {},
        "coverage": coverage,
        "viewer_sessions": viewer,
        "non_numeric_gates": non_numeric,
        "resource_scope_matches_baseline": True,
        "backend": {"runtime": "metal"},
        "hashes": hashes or {"policy_sha256": _sha256(policy_path)},
    }
    if extra:
        record.update(extra)
    return record


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _views(psnr: float = 21.0, full_psnr: float = 20.5, ssim: float = 0.6) -> list[dict]:
    return [
        {"id": view_id, "psnr_db": psnr, "full_psnr_db": full_psnr, "ssim": ssim}
        for view_id in VIEW_IDS
    ]


def _rois(psnr: float = 20.0) -> dict:
    return {"outlet-1": {"psnr_db": psnr}}


def _roi_file(tmp_path: Path, ids: list[str] | None = None) -> Path:
    ids = ids or ["outlet-1"]
    path = tmp_path / "rois.json"
    path.write_text(json.dumps({"validation": {roi_id: {"left": 1, "top": 2, "right": 3, "bottom": 4} for roi_id in ids}}))
    return path


def _write(tmp_path: Path, name: str, record: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(record))
    return path


def _run_cli(tmp_path: Path, policy_path: Path, baseline: dict, candidate: dict, rois: Path | None = None) -> subprocess.CompletedProcess:
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("paired-review identity: test-agent\n")
    baseline_path = _write(tmp_path, "baseline.json", baseline)
    candidate_path = _write(tmp_path, "candidate.json", candidate)
    _run_cli.counter = getattr(_run_cli, "counter", 0) + 1
    out = tmp_path / f"result-{_run_cli.counter}.json"
    command = [
        sys.executable, str(CLI),
        "--policy", str(policy_path),
        "--baseline", str(baseline_path),
        "--candidate", str(candidate_path),
        "--out", str(out),
    ]
    if rois is not None:
        command += ["--rois", str(rois)]
    run = subprocess.run(command, cwd=REPO, capture_output=True, text=True, timeout=120)
    result = json.loads(out.read_text()) if out.exists() else None
    return run, result


def _fully_eligible(tmp_path, policy_path, psnr: float = 21.0) -> tuple[dict, Path]:
    record = _run_record(tmp_path, policy_path=policy_path, views=_views(psnr=psnr),
                         rois=_rois())
    return record, _roi_file(tmp_path)


def test_policy_loads_and_rejects_unknown_version(tmp_path):
    raw = json.loads(BASELINE_POLICY.read_text())
    raw["benchmark_version"] = 99
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw))
    from standardphysics_pipeline.render_efficiency.policy import PolicyError, load_policy
    with pytest.raises(PolicyError):
        load_policy(path)


def test_e01_nonfinite_metrics_never_produce_a_score(tmp_path):
    policy_path = _mini_policy(tmp_path)
    baseline, rois = _fully_eligible(tmp_path, policy_path)
    candidates = {"psnr_db": float("nan"), "full_psnr_db": float("inf"), "ssim": float("-inf")}
    for field, bad in candidates.items():
        views = _views()
        for view in views:
            view[field] = bad
        candidate = _run_record(tmp_path, policy_path=policy_path, views=views, rois=_rois())
        run, result = _run_cli(tmp_path, policy_path, baseline, candidate, rois)
        assert result["selection_score"] is None
        assert result["status"] in ("blocked_missing_evidence", "completed_rejected")
        assert run.returncode != 0 or result["status"] == "completed_rejected"


def test_e01_positive_infinity_improvement_never_clips_to_a_high_score(tmp_path):
    policy_path = _mini_policy(tmp_path)
    baseline, rois = _fully_eligible(tmp_path, policy_path)
    views = _views()
    for view in views:
        view["psnr_db"] = float("inf")
    candidate = _run_record(tmp_path, policy_path=policy_path, views=views, rois=_rois())
    _run_cli(tmp_path, policy_path, baseline, candidate, rois) is not None
    _, result = _run_cli(tmp_path, policy_path, baseline, candidate, rois)
    assert result["selection_score"] is None
    assert result["status"] in ("blocked_missing_evidence", "completed_rejected")


def test_e02_missing_extra_and_duplicate_views_fail(tmp_path):
    policy_path = _mini_policy(tmp_path)
    baseline, rois = _fully_eligible(tmp_path, policy_path)
    good_candidate = _run_record(tmp_path, policy_path=policy_path, views=_views(), rois=_rois())

    for broken in (
        _views()[:-1],                                          # one deleted view
        _views() + [{"id": "image-999999.jpg", "psnr_db": 21.0, "full_psnr_db": 20.5, "ssim": 0.6}],  # extra view
    ):
        candidate = copy.deepcopy(good_candidate)
        candidate["views"] = broken
        run, result = _run_cli(tmp_path, policy_path, baseline, candidate, rois)
        assert run.returncode != 0 or result["status"] in ("blocked_missing_evidence", "completed_rejected")
        assert result["selection_score"] is None

    duplicate = copy.deepcopy(good_candidate)
    duplicate["views"] = [dict(_views()[0])] + _views()
    run, result = _run_cli(tmp_path, policy_path, baseline, duplicate, rois)
    assert result["selection_score"] is None
    assert result["gate_results"] == {} or result["status"] in ("blocked_missing_evidence", "completed_rejected")


def test_e03_missing_roi_gate_and_tampered_hash_block_score(tmp_path):
    policy_path = _mini_policy(tmp_path)
    baseline, _ = _fully_eligible(tmp_path, policy_path)

    no_roi_candidate = _run_record(tmp_path, policy_path=policy_path, views=_views(), rois={})
    _, result = _run_cli(tmp_path, policy_path, baseline, no_roi_candidate, _roi_file(tmp_path))
    assert result["selection_score"] is None
    assert result["gate_results"]["critical_roi"]["passed"] is False

    tampered_candidate = _run_record(
        tmp_path, policy_path=policy_path, views=_views(), rois=_rois(),
        hashes={"policy_sha256": _sha256(policy_path), "inputs": {str(tmp_path / "missing.png"): "deadbeef"}},
    )
    _, result = _run_cli(tmp_path, policy_path, baseline, tampered_candidate, _roi_file(tmp_path))
    assert result["selection_score"] is None
    assert result["gate_results"]["non_numeric:frozen_input_split_evaluator_and_artifact_hashes_validate"]["passed"] is False


def test_e04_baseline_against_itself_scores_exactly_50(tmp_path):
    policy_path = _mini_policy(tmp_path)
    baseline, rois = _fully_eligible(tmp_path, policy_path)
    candidate = copy.deepcopy(baseline)
    run, result = _run_cli(tmp_path, policy_path, baseline, candidate, rois)
    assert run.returncode == 0
    assert result["selection_score"] == pytest.approx(50.0)
    assert result["status"] == "completed_rejected"
    assert result["score_reason"].startswith("score 50")


def test_e04_faster_blurred_candidate_fails_gates(tmp_path):
    policy_path = _mini_policy(tmp_path)
    baseline, rois = _fully_eligible(tmp_path, policy_path, psnr=25.0)
    candidate = _run_record(
        tmp_path, policy_path=policy_path, views=_views(psnr=12.0, full_psnr=11.0, ssim=0.3),
        resources={"cold_end_to_end_seconds": 10.0, "peak_footprint_bytes": 100_000_000,
                   "compressed_spz_bytes": 1_000_000, "footprint_method": "phys_footprint",
                   "includes_compressed_export": True},
        rois=_rois(psnr=11.0),
    )
    run, result = _run_cli(tmp_path, policy_path, baseline, candidate, rois)
    assert run.returncode == 0
    assert result["selection_score"] is None
    assert result["status"] == "completed_rejected"
    assert result["gate_results"]["psnr_mean"]["passed"] is False


def test_e05_threshold_boundary_and_policy_edit_flips_verdict(tmp_path):
    policy_path = _mini_policy(tmp_path)
    baseline, rois = _fully_eligible(tmp_path, policy_path, psnr=21.0)
    # Exactly at the policy's 0.2 dB drop -> passes; just outside -> fails.
    at_boundary = _run_record(tmp_path, policy_path=policy_path, views=_views(psnr=21.0 - 0.2), rois=_rois())
    just_outside = _run_record(tmp_path, policy_path=policy_path, views=_views(psnr=21.0 - 0.200001), rois=_rois())
    _, at_result = _run_cli(tmp_path, policy_path, baseline, at_boundary, rois)
    _, out_result = _run_cli(tmp_path, policy_path, baseline, just_outside, rois)
    assert at_result["gate_results"]["psnr_mean"]["passed"] is True
    assert out_result["gate_results"]["psnr_mean"]["passed"] is False

    # Edit only the fixture policy (no source edits): widening the gate to
    # 0.3 dB must flip the same evidence from fail to pass.
    lax_policy = _mini_policy(tmp_path / "lax", overrides={"gate.max_mean_psnr_drop_db": 0.3})
    lax_baseline = _run_record(tmp_path, policy_path=lax_policy, views=_views(psnr=21.0),
                               rois=_rois())
    lax_baseline["hashes"]["policy_sha256"] = _sha256(lax_policy)
    just_outside["hashes"]["policy_sha256"] = _sha256(lax_policy)
    _, flipped = _run_cli(tmp_path / "lax", lax_policy, lax_baseline, just_outside, _roi_file(tmp_path / "lax"))
    assert flipped["gate_results"]["psnr_mean"]["passed"] is True


def test_e05_each_numeric_threshold_tested_at_and_just_outside(tmp_path):
    policy_path = _mini_policy(tmp_path)
    gates = json.loads(policy_path.read_text())["hard_numeric_gates"]
    baseline, rois = _fully_eligible(tmp_path, policy_path)

    def probe(candidate, pellet_name):
        _, result = _run_cli(tmp_path, policy_path, baseline, candidate, rois)
        return result["gate_results"][pellet_name]["passed"]

    def candidate(**overrides):
        record = _run_record(tmp_path, policy_path=policy_path, views=_views(),
                             rois=_rois())
        for key, value in overrides.items():
            record[key] = value
        return record

    def mean_views(psnr: float = 21.0, full_psnr: float = 20.5, ssim: float = 0.6) -> list[dict]:
        return [
            {"id": view_id, "psnr_db": psnr, "full_psnr_db": full_psnr, "ssim": ssim}
            for view_id in VIEW_IDS
        ]

    def worst_views(worst: float) -> list[dict]:
        views = mean_views()
        views[0]["psnr_db"] = worst
        return views

    def at_boundary(base_value, threshold_value):
        # Largest float at-or-inside the boundary, so IEEE subtraction
        # slides one ULP toward "pass"; the outside probe is 1e-6 past it.
        return float(np.nextafter(np.float64(base_value) - np.float64(threshold_value), np.inf))

    def boundary_flips(pellet, at_record, outside_record):
        assert probe(at_record, pellet) is True, pellet
        assert probe(outside_record, pellet) is False, pellet

    boundary_flips("psnr_mean",
                   candidate(views=mean_views(psnr=at_boundary(21.0, gates["max_mean_psnr_drop_db"]))),
                   candidate(views=mean_views(psnr=at_boundary(21.0, gates["max_mean_psnr_drop_db"]) - 1e-6)))
    boundary_flips("full_psnr_mean",
                   candidate(views=mean_views(full_psnr=at_boundary(20.5, gates["max_mean_full_frame_psnr_drop_db"]))),
                   candidate(views=mean_views(full_psnr=at_boundary(20.5, gates["max_mean_full_frame_psnr_drop_db"]) - 1e-6)))
    boundary_flips("ssim_mean",
                   candidate(views=mean_views(ssim=at_boundary(0.6, gates["max_mean_ssim_drop"]))),
                   candidate(views=mean_views(ssim=at_boundary(0.6, gates["max_mean_ssim_drop"]) - 1e-6)))
    boundary_flips("worst_view",
                   candidate(views=worst_views(at_boundary(21.0, gates["max_each_view_psnr_drop_db"]))),
                   candidate(views=worst_views(at_boundary(21.0, gates["max_each_view_psnr_drop_db"]) - 1e-6)))
    boundary_flips("critical_roi",
                   candidate(rois=_rois(psnr=at_boundary(20.0, gates["max_each_critical_roi_psnr_drop_db"]))),
                   candidate(rois=_rois(psnr=at_boundary(20.0, gates["max_each_critical_roi_psnr_drop_db"]) - 1e-6)))
    boundary_flips("uncovered",
                   candidate(coverage={"alpha_available": True, "uncovered_fraction": float(np.nextafter(np.float64(0.05) + np.float64(gates["max_supported_uncovered_fraction_increase"]), -np.inf))}),
                   candidate(coverage={"alpha_available": True, "uncovered_fraction": float(np.nextafter(np.float64(0.05) + np.float64(gates["max_supported_uncovered_fraction_increase"]), -np.inf)) + 1e-6}))
    frame_record = lambda p95: {"required": 3, "recorded": 3, "gpu_identity": "metal",
                                "completed_frame_samples": 300, "worst_session_p95_ms": p95}
    boundary_flips("frame_p95",
                   candidate(viewer_sessions=frame_record(float(np.nextafter(np.float64(16.0) * np.float64(gates["max_frame_p95_ratio_candidate_to_baseline"]), -np.inf)))),
                   candidate(viewer_sessions=frame_record(float(np.nextafter(np.float64(16.0) * np.float64(gates["max_frame_p95_ratio_candidate_to_baseline"]), -np.inf)) + 1e-6)))


def test_e06_one_repeat_or_rss_or_missing_compression_blocks_resources(tmp_path):
    policy_path = _mini_policy(tmp_path)
    baseline, rois = _fully_eligible(tmp_path, policy_path)

    one_repeat = _run_record(tmp_path, policy_path=policy_path, views=_views(), rois=_rois(),
                             repeats=[{"index": 0}])
    _, result = _run_cli(tmp_path, policy_path, baseline, one_repeat, rois)
    assert result["gate_results"]["resources_candidate"]["passed"] is False

    rss_candidate = _run_record(tmp_path, policy_path=policy_path, views=_views(), rois=_rois(),
                                resources={"cold_end_to_end_seconds": 120.0,
                                           "peak_footprint_bytes": 999_999_999,
                                           "compressed_spz_bytes": 9_999_999,
                                           "footprint_method": "rss",
                                           "includes_compressed_export": True})
    _, result = _run_cli(tmp_path, policy_path, baseline, rss_candidate, rois)
    assert result["gate_results"]["resources_candidate"]["passed"] is False

    no_compression = _run_record(tmp_path, policy_path=policy_path, views=_views(), rois=_rois(),
                                 resources={"cold_end_to_end_seconds": 120.0,
                                            "peak_footprint_bytes": 999_999_999,
                                            "includes_compressed_export": False,
                                            "footprint_method": "phys_footprint"})
    _, result = _run_cli(tmp_path, policy_path, baseline, no_compression, rois)
    assert result["gate_results"]["resources_candidate"]["passed"] is False


def test_e06_missing_repeat_and_stale_metric_blocks_selection(tmp_path):
    policy_path = _mini_policy(tmp_path)
    baseline, rois = _fully_eligible(tmp_path, policy_path)
    # A baseline record without any repeats cannot be compared either.
    baseline_no_repeats = copy.deepcopy(baseline)
    baseline_no_repeats["repeats"] = []
    candidate = _run_record(tmp_path, policy_path=policy_path, views=_views(), rois=_rois(),
                            resources={"cold_end_to_end_seconds": 120.0,
                                       "peak_footprint_bytes": 1_000_000_000,
                                       "compressed_spz_bytes": 10_000_000,
                                       "includes_compressed_export": True,
                                       "footprint_method": "phys_footprint"})
    _, result = _run_cli(tmp_path, policy_path, baseline_no_repeats, candidate, rois)
    assert result["gate_results"]["resources_base"]["passed"] is False
    assert result["selection_score"] is None


def test_e03_false_or_unknown_gate_blocks_score(tmp_path):
    policy_path = _mini_policy(tmp_path)
    baseline, rois = _fully_eligible(tmp_path, policy_path)
    for broken_gate in (False, None):
        non_numeric = {
            "originals_and_previous_revisions_preserved": {
                "passed": True, "evidence_paths": [str(tmp_path / "evidence.txt")],
            },
            "frozen_input_split_evaluator_and_artifact_hashes_validate": {
                "passed": broken_gate, "evidence_paths": [str(tmp_path / "evidence.txt")],
            },
        }
        candidate = _run_record(tmp_path, policy_path=policy_path, views=_views(), rois=_rois(),
                                non_numeric=non_numeric)
        _, result = _run_cli(tmp_path, policy_path, baseline, candidate, rois)
        assert result["selection_score"] is None
        assert result["gate_results"]["non_numeric:frozen_input_split_evaluator_and_artifact_hashes_validate"]["passed"] is False


def test_cli_exit_codes_and_result_destination_refusal(tmp_path):
    policy_path = _mini_policy(tmp_path)
    baseline, rois = _fully_eligible(tmp_path, policy_path)

    # invalid policy JSON -> exit 2
    bad_policy = tmp_path / "bad-policy.json"
    bad_policy.write_text("{not json")
    run = subprocess.run(
        [sys.executable, str(CLI), "--policy", str(bad_policy), "--baseline", str(_write(tmp_path, "b2.json", baseline)),
         "--candidate", str(_write(tmp_path, "c2.json", baseline)), "--out", str(tmp_path / "r2.json")],
        cwd=REPO, capture_output=True, text=True, timeout=120)
    assert run.returncode == 2

    # missing views -> nonzero (invalid evidence)
    short_baseline = copy.deepcopy(baseline)
    short_baseline["views"] = []
    run, result = _run_cli(tmp_path, policy_path, short_baseline, baseline, rois)
    assert result["selection_score"] is None

    # existing out file -> refuse (exit 2)
    out = tmp_path / "result.json"
    subprocess.run(
        [sys.executable, str(CLI), "--policy", str(policy_path), "--baseline", str(_write(tmp_path, "b3.json", baseline)),
         "--candidate", str(_write(tmp_path, "c3.json", baseline)), "--out", str(out)],
        cwd=REPO, capture_output=True, text=True, timeout=120)
    run = subprocess.run(
        [sys.executable, str(CLI), "--policy", str(policy_path), "--baseline", str(_write(tmp_path, "b4.json", baseline)),
         "--candidate", str(_write(tmp_path, "c4.json", baseline)), "--out", str(out)],
        cwd=REPO, capture_output=True, text=True, timeout=120)
    assert run.returncode == 2 and "refusing" in run.stderr
