"""End-to-end image orientation regression (Q M06).

The detector contract has two halves that DET-01 cannot pin by arithmetic
alone: `encode_frame` must turn the stored sensor JPEG upright using the
interface orientation before the model sees it, and every box must come back
into stored sensor pixels. This pins the bytes instead: a stored asymmetric
photograph built from a known upright patch, read by a transport that sees
exactly the JPEG the pipeline produced.

Each orientation case asserts
1. the received JPEG has the upright dimensions, and the patch is where the
   upright image says it is - so a mutant that zeroes the turns ships the
   image sideways and fails before any mapping is consulted;
2. the detection box round-trips into stored sensor pixels and its crop
   covers the patch drawn on the stored image.

The ground truth is built with PIL's standard quarter-turn constants in the
test itself, not with the pipeline's tables, so a wrong turn direction or a
swapped axis fails on the bytes too.
"""

from __future__ import annotations

import base64
import io
import json
import pathlib

import numpy as np
import pytest
from PIL import Image
from standardphysics_pipeline.discovery.detect import detect_objects

PATCH_RGB = (255, 0, 128)

CASES = {
    "landscape_right": dict(turns=0, upright=(800, 600), rect=(620, 60, 700, 110)),
    "portrait": dict(turns=1, upright=(600, 800), rect=(450, 60, 520, 110)),
    "landscape_left": dict(turns=2, upright=(800, 600), rect=(120, 60, 200, 110)),
    "portrait_upside_down": dict(turns=3, upright=(600, 800), rect=(120, 600, 190, 650)),
}
"""turns = clockwise quarter turns the stored sensor image needs to stand upright.

Each patch sits off-centre, so any wrong turn (or none at all) moves it
visibly and fails the received-image assertion.
"""


def _stored_fixture(tmp_path: pathlib.Path, turns: int, upright_size, rect) -> pathlib.Path:
    """The stored sensor JPEG: the upright canvas with its patch, turned back CCW.

    How a phone stores an upright photo on its side: the inverse of what
    encode_frame does when it stands the frame upright clockwise.
    """
    width, height = upright_size
    upright = Image.new("RGB", (width, height), (200, 210, 200))
    for col in range(rect[0], rect[2]):
        for row in range(rect[1], rect[3]):
            upright.putpixel((col, row), PATCH_RGB)
    stored = upright
    for _ in range(turns):
        stored = stored.transpose(Image.Transpose.ROTATE_90)
    path = tmp_path / f"stored-{turns}.jpg"
    stored.save(path, format="JPEG", quality=95)
    return path


def _stored_rect(upright_size, rect, turns) -> tuple[float, float, float, float]:
    """The patch rectangle in the stored image, computed independently with PIL."""
    width, height = upright_size
    canvas = Image.new("L", (width, height), 0)
    for col in range(rect[0], rect[2]):
        for row in range(rect[1], rect[3]):
            canvas.putpixel((col, row), 255)
    for _ in range(turns):
        canvas = canvas.transpose(Image.Transpose.ROTATE_90)
    mask = np.asarray(canvas) > 0
    rows, cols = np.nonzero(mask)
    return float(cols.min()), float(rows.min()), float(cols.max()), float(rows.max())


def _patch_mask(image) -> np.ndarray:
    arr = np.asarray(image)
    return (arr[:, :, 0] > 200) & (arr[:, :, 1] < 80) & (arr[:, :, 2] > 80) & (arr[:, :, 2] < 180)


class TestEncodeFrameOrientationEndToEnd:
    @pytest.mark.parametrize("orientation", sorted(CASES))
    def test_patch_round_trips_upright_and_back_to_sensor_pixels(self, tmp_path: pathlib.Path, orientation: str):
        case = CASES[orientation]
        turns, upright_size, rect = case["turns"], case["upright"], case["rect"]
        stored_path = _stored_fixture(tmp_path, turns, upright_size, rect)
        expected_sensor = _stored_rect(upright_size, rect, turns)

        seen: dict = {}

        def detector_eyes(url, body, headers):
            """The model, reading exactly the bytes encode_frame produced."""
            image_url = body["messages"][1]["content"][1]["image_url"]["url"]
            received = Image.open(io.BytesIO(base64.b64decode(image_url.split(",", 1)[1]))).convert("RGB")
            seen["size"] = received.size
            mask = _patch_mask(received)
            rows, cols = np.nonzero(mask)
            seen["box_received"] = (float(cols.min()), float(rows.min()), float(cols.max()), float(rows.max()))
            width, height = received.size
            box_2d = [
                round(float(rows.min()) / height * 1000),
                round(float(cols.min()) / width * 1000),
                round(float(rows.max()) / height * 1000),
                round(float(cols.max()) / width * 1000),
            ]
            return {"choices": [{"message": {"content": json.dumps({"objects": [
                {"name": "object", "box_2d": box_2d, "movable": True, "confidence": 0.9},
            ]})}}]}

        detections = detect_objects(stored_path, "frame-0001", orientation=orientation, transport=detector_eyes)

        # The frame the model saw really was stood upright, patch where it belongs.
        assert seen["size"] == upright_size, f"{orientation}: model saw {seen['size']}, not upright {upright_size}"
        seen_left, seen_top, seen_right, seen_bottom = seen["box_received"]
        seen_centre = ((seen_left + seen_right) / 2, (seen_top + seen_bottom) / 2)
        upright_centre = ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)
        assert seen_centre == pytest.approx(upright_centre, abs=4), (
            f"{orientation}: patch seen at {seen_centre}, expected {upright_centre} upright"
        )

        # The box came back into stored sensor pixels over the same patch.
        assert len(detections) == 1
        box = detections[0].box
        assert box[0] == pytest.approx(expected_sensor[0], abs=4), f"{orientation}: left {box[0]} vs {expected_sensor[0]}"
        assert box[1] == pytest.approx(expected_sensor[1], abs=4), f"{orientation}: top {box[1]} vs {expected_sensor[1]}"
        assert box[2] == pytest.approx(expected_sensor[2], abs=4), f"{orientation}: right {box[2]} vs {expected_sensor[2]}"
        assert box[3] == pytest.approx(expected_sensor[3], abs=4), f"{orientation}: bottom {box[3]} vs {expected_sensor[3]}"

        with Image.open(stored_path) as stored:
            crop = stored.crop(tuple(int(round(value)) for value in box))
        centre = crop.getpixel((crop.width // 2, crop.height // 2))
        assert centre[0] > 200 and centre[1] < 80 and 80 < centre[2] < 180, (
            f"{orientation}: sensor crop centre {centre} is not the patch"
        )
