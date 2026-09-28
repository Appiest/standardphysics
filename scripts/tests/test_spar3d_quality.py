import uuid
from types import SimpleNamespace

import pytest

from scripts import spar3d_furniture_experiment as experiment
from standardphysics_api.furniture import furniture_class

accepted_for_display = experiment.accepted_for_display


def test_photo_label_selects_furniture_type_for_refinement():
    corrected_table = SimpleNamespace(label="Table", raw_category="sofa", labeled_by="discovery")
    discovered_sofa = SimpleNamespace(label="Sofa", raw_category="storage", labeled_by="discovery")
    assert furniture_class(corrected_table) == "table"
    assert furniture_class(discovered_sofa) == "sofa"


def test_small_visual_gain_cannot_override_bad_measured_geometry():
    record = {
        "box_iou": 1.0,
        "lidar_to_surface_cm": 11.43,
        "texture_painted_fraction": 0.447,
        "photo_ssim_mean": 0.636,
        "views": [{"lidar_ssim": 0.621} for _ in range(5)],
    }
    assert not accepted_for_display(record)


def test_good_geometry_still_needs_five_independent_views():
    record = {
        "box_iou": 0.9,
        "lidar_to_surface_cm": 3.0,
        "texture_painted_fraction": 0.7,
        "photo_ssim_mean": 0.7,
        "views": [{"lidar_ssim": 0.6} for _ in range(4)],
    }
    assert not accepted_for_display(record)
    record["views"].append({"lidar_ssim": 0.6})
    assert accepted_for_display(record)


def test_batch_reuses_room_evidence_and_retries_only_failed_candidates(tmp_path, monkeypatch):
    first, second = uuid.uuid4(), uuid.uuid4()
    loads, calls = [], []

    def room_evidence(scan_id):
        loads.append(scan_id)
        return object()

    def run_evidence(room, node_id, directory):
        calls.append(node_id)
        return {
            "node_id": str(node_id),
            "status": "failed" if node_id == second and calls.count(second) == 1 else "skipped",
        }

    monkeypatch.setattr(experiment, "room_evidence", room_evidence)
    monkeypatch.setattr(experiment, "run_evidence", run_evidence)
    scan_id = uuid.uuid4()
    experiment.run_batch(scan_id, [first, second], tmp_path)
    experiment.run_batch(scan_id, [first, second], tmp_path)
    assert loads == [scan_id, scan_id]
    assert calls == [first, second, second]


def test_missing_heldout_photos_skip_candidate_before_inference(tmp_path, monkeypatch):
    node_id = uuid.uuid4()
    room = SimpleNamespace(graph=SimpleNamespace(nodes=[SimpleNamespace(id=node_id)]))
    evidence = SimpleNamespace(crop=object(), crop_frame_id="frame-1")
    monkeypatch.setattr(experiment, "prepare_object", lambda *_: (evidence, {"node_id": str(node_id)}))

    def no_heldout_photos(*_):
        raise experiment.InsufficientHeldoutViews("at least five distinct held-out calibrated photos are required")

    monkeypatch.setattr(experiment, "heldout_cameras", no_heldout_photos)
    monkeypatch.setattr(experiment, "weights_available", lambda: pytest.fail("inference should not start"))
    result = experiment.run_evidence(room, node_id, tmp_path)
    assert result["status"] == "skipped"
    assert "held-out" in result["reason"]
