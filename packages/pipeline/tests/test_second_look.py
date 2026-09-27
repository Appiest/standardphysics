"""An object the photos never agreed on is named by one look at its best close-ups."""

from __future__ import annotations

import json
from collections import Counter

import numpy as np
import pytest
from PIL import Image
from standardphysics_pipeline.discovery.carve import fit_box
from standardphysics_pipeline.discovery.detect import DetectionSchemaError
from standardphysics_pipeline.discovery.merge import DiscoveredObject
from standardphysics_pipeline.discovery.second_look import Photos, _sharpness, second_look, undecided
from standardphysics_pipeline.textures.camera import PhotoCamera

SPLIT_VOTES = Counter({"chair": 1.8, "payment terminal": 0.9, "sign": 0.9, "tablet": 0.8})
LEANING_VOTES = Counter({"refrigerator": 1.8, "glass door refrigerator": 0.9, "ceiling light": 0.9, "shelving": 0.8})
LEANING_DISPENSER = Counter({"sanitizer dispenser": 1.8, "hand sanitizer dispenser": 0.9, "chair": 1.5})
AGREED_VOTES = Counter({"bar stool": 5.0, "sign": 0.5})


def camera_at(position, looking_at, frame_id="frame-0001", width=640, height=480, focal=500.0) -> PhotoCamera:
    forward = np.asarray(looking_at, dtype=float) - np.asarray(position, dtype=float)
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    rotation = np.stack([right, np.cross(forward, right), forward])
    return PhotoCamera(
        frame_id=frame_id,
        room_to_camera=np.vstack([
            np.hstack([rotation, (-rotation @ np.asarray(position, dtype=float)).reshape(3, 1)]),
            [0.0, 0.0, 0.0, 1.0],
        ]),
        fx=focal, fy=focal, cx=width / 2 - 0.5, cy=height / 2 - 0.5, width=width, height=height, timestamp=0.0,
    )


def dispenser(weights: Counter) -> DiscoveredObject:
    points = np.stack(np.meshgrid(*[np.arange(c - s / 2, c + s / 2 + 0.01, 0.02)
                                    for c, s in zip((2.0, 0.0, 1.3), (0.2, 0.2, 0.45))], indexing="ij"), axis=-1)
    box = fit_box(points.reshape(-1, 3))
    return DiscoveredObject(name=weights.most_common(1)[0][0], box=box, movable=True, confidence=0.8,
                            frame_ids=("frame-0001",), weights=weights)


@pytest.fixture
def photos(tmp_path) -> Photos:
    path = tmp_path / "frame_0001.jpg"
    Image.new("RGB", (640, 480), (200, 180, 160)).save(path)
    return Photos([camera_at((0.0, 0.0, 1.3), (2.0, 0.0, 1.3))], {"frame-0001": path}, {"frame-0001": ""})


class Model:
    def __init__(self, name="hand sanitizer dispenser", fails=False):
        self.name, self.fails, self.bodies = name, fails, []

    def __call__(self, url, body, headers):
        self.bodies.append(body)
        if self.fails:
            raise DetectionSchemaError("the model refused the request")
        return {"choices": [{"message": {"content": json.dumps({"name": self.name, "movable": True})}}]}


def test_votes_split_across_names_are_undecided_and_a_clear_lead_is_not():
    assert undecided(dispenser(SPLIT_VOTES))
    assert not undecided(dispenser(AGREED_VOTES))


def test_an_undecided_object_is_named_from_its_close_ups(photos):
    model = Model()
    [named] = second_look([dispenser(SPLIT_VOTES)], photos, transport=model)
    assert named.name == "hand sanitizer dispenser"
    [body] = model.bodies
    content = body["messages"][1]["content"]
    assert "chair" not in content[0]["text"] and content[1]["type"] == "image_url"


def test_an_object_the_photos_agreed_on_is_not_asked_about(photos):
    model = Model()
    [kept] = second_look([dispenser(AGREED_VOTES)], photos, transport=model)
    assert kept.name == "bar stool" and model.bodies == []


def test_a_failed_second_look_keeps_the_photos_name(photos):
    [kept] = second_look([dispenser(SPLIT_VOTES)], photos, transport=Model(fails=True))
    assert kept.name == "chair"


def test_an_object_no_photo_shows_is_not_asked_about(photos):
    behind = Photos([camera_at((0.0, 0.0, 1.3), (-2.0, 0.0, 1.3))], photos.paths, photos.orientations)
    model = Model()
    [kept] = second_look([dispenser(SPLIT_VOTES)], behind, transport=model)
    assert kept.name == "chair" and model.bodies == []


def test_a_rebuild_reuses_the_answer_instead_of_asking_again(photos, tmp_path):
    first, again = Model(), Model(name="something else")
    second_look([dispenser(SPLIT_VOTES)], photos, transport=first, cache_dir=tmp_path / "cache")
    [named] = second_look([dispenser(SPLIT_VOTES)], photos, transport=again, cache_dir=tmp_path / "cache")
    assert named.name == "hand sanitizer dispenser" and again.bodies == []


def test_a_second_look_never_makes_something_a_counter(photos):
    [kept] = second_look([dispenser(SPLIT_VOTES)], photos, transport=Model(name="counter"))
    assert kept.name == "chair"


def test_a_sharp_close_up_ranks_above_a_blurred_one():
    import io

    from PIL import ImageFilter

    stripes = Image.new("L", (128, 128))
    stripes.putdata([255 if (x // 4) % 2 else 0 for y in range(128) for x in range(128)])
    sharp, blurred = io.BytesIO(), io.BytesIO()
    stripes.save(sharp, format="JPEG")
    stripes.filter(ImageFilter.GaussianBlur(6)).save(blurred, format="JPEG")
    assert _sharpness(sharp.getvalue()) > 10 * _sharpness(blurred.getvalue())


def test_leaning_votes_let_a_second_look_refine_the_name_but_not_the_kind(photos):
    [fridge] = second_look([dispenser(LEANING_VOTES)], photos, transport=Model(name="paper towel dispenser"))
    [napkins] = second_look([dispenser(LEANING_DISPENSER)], photos, transport=Model(name="napkin dispenser"))
    assert fridge.name == "refrigerator" and napkins.name == "napkin dispenser"
