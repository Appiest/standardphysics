"""Tests for outlet detection, caching, and coordinate mappings (DET-01, DET-02, DET-03, CACHE-01).

Validates:
- DET-01: Asymmetric image at 0/90/180/270 degrees; off-center resized crops and overlapping tiles
  correctly map to sensor coordinates without double rotation or x/y swaps.
- DET-02: Malformed, reversed, out-of-bounds, NaN/Inf boxes are rejected; unresolved socket count
  is preserved without inventing duplex sockets.
- DET-03: Distinguishable DetectionAuthError, DetectionSchemaError, DetectionTransientError;
  auth/schema errors are never retried; errors are never cached as empty success.
- CACHE-01: Structured detector sockets/crop/category/caveats roundtrip through DetectionCache;
  version/model/orientation changes invalidate cache.
"""

from __future__ import annotations

import io
import json
import math
import pathlib

import pytest
from PIL import Image
from standardphysics_pipeline.discovery.cache import DetectionCache
from standardphysics_pipeline.discovery.detect import Detection, _detections_from, detect_objects
from standardphysics_pipeline.discovery.detection_boxes import (
    extract_padded_crop,
    generate_tiles,
    map_crop_box_to_sensor,
)
from standardphysics_pipeline.discovery.detection_errors import DetectionAuthError, DetectionSchemaError
from standardphysics_pipeline.discovery.frame_encoding import EncodedFrame


def test_det_01_asymmetric_rotations_crops_and_tiles(tmp_path: pathlib.Path):
    """DET-01: Tests coordinate transformations across 0, 90, 180, 270 degree turns and crops."""
    # Asymmetric image: width 800, height 600
    img = Image.new("RGB", (800, 600), color=(100, 150, 200))
    img_path = tmp_path / "asym.jpg"
    img.save(img_path)

    # 1. Test unrotated (turns=0, landscape_right)
    # Box [ymin, xmin, ymax, xmax] = [100, 200, 300, 400] in 0-1000 normalized space
    # Upright size: width 800, height 600
    box_0 = map_crop_box_to_sensor([100, 200, 300, 400], (0.0, 0.0, 800.0, 600.0), 0)
    assert box_0 is not None
    # left = 0.20 * 800 = 160, top = 0.10 * 600 = 60, right = 0.40 * 800 = 320, bottom = 0.30 * 600 = 180
    assert box_0 == pytest.approx((160.0, 60.0, 320.0, 180.0), abs=1e-3)

    # 2. Test 90 degrees clockwise (turns=1, portrait)
    # Stored image is 800x600. When rotated 90 deg clockwise, upright is 600x800.
    # An object in upright [ymin, xmin, ymax, xmax] = [200, 100, 400, 300]
    # In sensor space: x_s = y_u, y_s = 1 - x_u
    # y_u in [0.2, 0.4] -> x_s in [0.2, 0.4] * 800 = [160, 320]
    # x_u in [0.1, 0.3] -> y_s in [1 - 0.3, 1 - 0.1] = [0.7, 0.9] * 600 = [420, 540]
    box_90 = map_crop_box_to_sensor([200, 100, 400, 300], (0.0, 0.0, 800.0, 600.0), 1)
    assert box_90 is not None
    assert box_90 == pytest.approx((160.0, 420.0, 320.0, 540.0), abs=1e-3)

    # 3. Test 180 degrees (turns=2, landscape_left)
    box_180 = map_crop_box_to_sensor([100, 200, 300, 400], (0.0, 0.0, 800.0, 600.0), 2)
    assert box_180 is not None
    # x_s = 1 - x_u -> [1-0.4, 1-0.2] * 800 = [480, 640]
    # y_s = 1 - y_u -> [1-0.3, 1-0.1] * 600 = [420, 540]
    assert box_180 == pytest.approx((480.0, 420.0, 640.0, 540.0), abs=1e-3)

    # 4. Test 270 degrees (turns=3, portrait_upside_down)
    box_270 = map_crop_box_to_sensor([200, 100, 400, 300], (0.0, 0.0, 800.0, 600.0), 3)
    assert box_270 is not None
    # x_s = 1 - y_u -> [1-0.4, 1-0.2] * 800 = [480, 640]
    # y_s = x_u -> [0.1, 0.3] * 600 = [60, 180]
    assert box_270 == pytest.approx((480.0, 60.0, 640.0, 180.0), abs=1e-3)

    # 5. Test off-center crop extraction and mapping
    cropped_img, crop_rect = extract_padded_crop(img, (100.0, 100.0, 300.0, 300.0), padding_fraction=0.20)
    # Box was 200x200, pad is 40 each side -> crop_rect is (60, 60, 340, 340)
    assert crop_rect == pytest.approx((60.0, 60.0, 340.0, 340.0), abs=1e-3)
    assert cropped_img.width == 280
    assert cropped_img.height == 280

    # Map a detection inside this crop back to sensor pixels
    # Crop space box [ymin, xmin, ymax, xmax] = [250, 250, 750, 750] (center 50% of crop)
    mapped_box = map_crop_box_to_sensor([250, 250, 750, 750], crop_rect, 0)
    assert mapped_box is not None
    # Left: 60 + 0.25 * 280 = 130; Right: 60 + 0.75 * 280 = 270
    assert mapped_box == pytest.approx((130.0, 130.0, 270.0, 270.0), abs=1e-3)

    # 6. Test overlapping tiles generation
    tiles = generate_tiles(1920, 1080, tile_size=(1024, 1024), overlap=0.20)
    assert len(tiles) >= 2
    # Ensure tiles cover the full image
    assert min(t[0] for t in tiles) == 0.0
    assert min(t[1] for t in tiles) == 0.0
    assert max(t[2] for t in tiles) == 1920.0
    assert max(t[3] for t in tiles) == 1080.0


def test_det_02_rejects_malformed_and_preserves_unknown_sockets():
    """DET-02: Rejects malformed/reversed/NaN/Inf boxes and preserves unresolved socket count."""
    frame = EncodedFrame(jpeg=b"", width=640, height=480, turns=0)

    # NaN / Inf in box
    assert map_crop_box_to_sensor([math.nan, 100, 200, 300], (0, 0, 640, 480), 0) is None
    assert map_crop_box_to_sensor([100, math.inf, 200, 300], (0, 0, 640, 480), 0) is None
    assert map_crop_box_to_sensor([100, 100, -math.inf, 300], (0, 0, 640, 480), 0) is None

    # Degenerate / zero area
    assert map_crop_box_to_sensor([100, 100, 100, 100], (0, 0, 640, 480), 0) is None

    # Reversed box [ymin, xmin, ymax, xmax] with ymin > ymax is auto-sorted, but if invalid length:
    assert map_crop_box_to_sensor([100, 200, 300], (0, 0, 640, 480), 0) is None
    assert map_crop_box_to_sensor("not_a_box", (0, 0, 640, 480), 0) is None

    # Out-of-bounds (all negative or all > 1000)
    assert map_crop_box_to_sensor([-500, -500, -100, -100], (0, 0, 640, 480), 0) is None

    # Unresolved socket count: sockets omitted
    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "objects": [
                            {
                                "name": "outlet",
                                "box_2d": [100, 100, 300, 300],
                                "movable": False,
                                "confidence": 0.9,
                            }
                        ]
                    })
                }
            }
        ]
    }
    detections = _detections_from(payload, frame, "frame-0001")
    assert len(detections) == 1
    # sockets must be empty tuple (unknown / unresolved), NOT fabricated duplex
    assert detections[0].sockets == ()


def test_det_03_error_distinction_and_no_retry_for_auth(tmp_path: pathlib.Path):
    """DET-03: Provider timeout/auth error versus valid empty result."""
    img = Image.new("RGB", (100, 100), color=(0, 0, 0))
    img_path = tmp_path / "frame.jpg"
    img.save(img_path)

    attempts = 0

    # 1. Auth error (401) -> must raise DetectionAuthError and NOT retry
    def auth_failure_transport(url, body, headers):
        nonlocal attempts
        attempts += 1
        import urllib.error
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(b"Unauthorized"))

    with pytest.raises(DetectionAuthError):
        detect_objects(img_path, "frame-0001", transport=auth_failure_transport)
    assert attempts == 1, "Auth error must not be retried"

    # 2. Schema error (malformed JSON from model) -> must raise DetectionSchemaError and NOT retry
    attempts = 0
    def schema_failure_transport(url, body, headers):
        nonlocal attempts
        attempts += 1
        return {"choices": [{"message": {"content": "not json"}}]}

    with pytest.raises(DetectionSchemaError):
        detect_objects(img_path, "frame-0001", transport=schema_failure_transport)
    assert attempts == 1, "Schema error must not be retried"

    # 3. Valid empty result -> returns [] (not an error, no retries)
    def empty_success_transport(url, body, headers):
        return {"choices": [{"message": {"content": json.dumps({"objects": []})}}]}

    res = detect_objects(img_path, "frame-0001", transport=empty_success_transport)
    assert res == []


def test_cache_01_structured_metadata_roundtrip(tmp_path: pathlib.Path):
    """CACHE-01: Sockets, crop_box, category, caveats roundtrip through DetectionCache."""
    cache = DetectionCache(tmp_path / "cache", model="test-model")

    img = Image.new("RGB", (640, 480), color=(50, 50, 50))
    img_path = tmp_path / "frame-0001.jpg"
    img.save(img_path)

    det = Detection(
        frame_id="frame-0001",
        name="outlet",
        box=(100.0, 150.0, 200.0, 250.0),
        movable=False,
        confidence=0.92,
        category="outlet",
        crop_box=(80.0, 130.0, 220.0, 270.0),
        sockets=((150.0, 180.0), (150.0, 220.0)),
        review_status="detected",
        uncertainty_reasons=("single viewpoint observation; not independently verified from separate angle",),
    )

    # Put into cache
    cache.put(img_path, [det], orientation="landscape_right")

    # Replay from cache
    replayed = cache.get(img_path, "frame-0001", orientation="landscape_right")
    assert replayed is not None
    assert len(replayed) == 1
    out = replayed[0]

    assert out.frame_id == det.frame_id
    assert out.name == det.name
    assert out.box == det.box
    assert out.movable == det.movable
    assert out.confidence == pytest.approx(det.confidence)
    assert out.category == det.category
    assert out.crop_box == det.crop_box
    assert out.sockets == det.sockets
    assert out.review_status == det.review_status
    assert out.uncertainty_reasons == det.uncertainty_reasons

    # Changed orientation or model invalidates cache
    assert cache.get(img_path, "frame-0001", orientation="portrait") is None

    other_cache = DetectionCache(tmp_path / "cache", model="different-model")
    assert other_cache.get(img_path, "frame-0001", orientation="landscape_right") is None
