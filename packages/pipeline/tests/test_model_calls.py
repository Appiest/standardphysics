"""The pipeline reports each hosted model call it makes, with a summary that holds no key, address or photo."""

import json

import pytest
from PIL import Image
from standardphysics_pipeline import model_calls
from standardphysics_pipeline.discovery.detect import detect_objects

ONE_TERMINAL = {"objects": [
    {"name": "payment terminal", "box_2d": [400, 400, 600, 600], "movable": True, "confidence": 0.9},
]}


@pytest.fixture
def reports(monkeypatch):
    seen = []

    def reporter(name, summary, call):
        seen.append((name, summary))
        return call()

    monkeypatch.setattr(model_calls, "_reporter", reporter)
    return seen


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "frame-0001.jpg"
    Image.new("RGB", (64, 48), (120, 90, 60)).save(path)
    return path


def test_a_call_goes_straight_through_until_a_reporter_is_installed(monkeypatch):
    monkeypatch.setattr(model_calls, "_reporter", model_calls._unreported)
    assert model_calls.reported("model.detect", {}, lambda: 7) == 7


def test_images_are_counted_and_nothing_else_is_read():
    messages = [
        {"role": "system", "content": "Name what is in the photo."},
        {"role": "user", "content": [{"type": "text", "text": "frame 1"},
                                     {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}]},
    ]
    assert model_calls.images_in(messages) == 1


def test_a_detection_is_reported_once_with_its_photo_counted_not_carried(reports, photo, monkeypatch):
    monkeypatch.setenv("DISCOVERY_API_KEY", "sk-detector-secret")

    def transport(url, body, headers):
        return {"choices": [{"message": {"content": json.dumps(ONE_TERMINAL)}}]}

    assert [one.name for one in detect_objects(photo, "frame-0001", transport=transport)] == ["payment terminal"]
    assert [name for name, _ in reports] == ["model.detect"]
    summary = json.dumps(reports[0][1])
    assert reports[0][1]["images"] == 1
    assert "base64" not in summary and "sk-detector-secret" not in summary
