"""A second look at an object whose name the photos never agreed on.

The detector names what it sees in one frame, often small and at an angle, and
across a walk the same object collects a spread of names. Most spreads settle:
"sanitizer", "hand sanitizer" and "sanitizer dispenser" pool into one name. Some
never do. On Share-Tea a hand-sanitizer dispenser on the bar ledge collected
chair, dispenser, payment terminal, sign, tablet and speaker in six walks, and
whichever name happened to lead decided whether the reach check ever saw it.

So an object whose leading name has less than three quarters of the support
behind it is looked at again: its best close-ups, cut from the photos that saw it
largest, go to the vision model together with one question, what is this.
The answer replaces the name. Every other object keeps the name its votes gave
it, and a second look that fails keeps it too.

Four things this learned on Share-Tea. The earlier guesses are not shown: given
"payment terminal" among them, the model picked it for a napkin dispenser it
named correctly on its own. The close-ups are the sharpest of the largest
views, because a walk blurs many frames and a blurred one reads as anything.
And a second look never makes something a counter or a table: a blurred crop
of a kitchen pillar came back as a counter 81 inches high, and a work surface
needs the mesh to show one, not a name. The model is asked at temperature zero
and may answer 'unclear': sampled freely, the same fridge-top crops came back
as a paper towel dispenser once and a napkin dispenser the next time.
"""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import io
import json
import logging
import math
import os
import pathlib
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from ..textures.camera import PhotoCamera
from . import detect
from .carve import CarvedBox
from .merge import DiscoveredObject, name_support
from .semantic_corrections import is_work_surface

log = logging.getLogger(__name__)

SPLIT = 0.75
"""Share of the support the leading name must hold for the votes to count as agreed.

At one half, "sanitizer" held 57% of a napkin dispenser's votes, tied with "chair", and was never
looked at again. A second look costs about a second and runs alongside the others."""
OPEN = 0.5
"""Below this share the votes are split and a second look may name any kind of thing; above it, while
still under SPLIT, the votes lean one way and a second look may only say which of that kind it is.
The top 24 cm of a kitchen fridge, voted 'refrigerator' at 66%, came back as a paper towel dispenser."""
CLOSE_UPS = 3
LARGEST_VIEWS = 8
"""Views considered before the sharpest CLOSE_UPS of them are kept."""
PADDING = 0.35
"""How much of the object's own size is kept round it, so the model sees what it stands on."""
MIN_CLOSE_UP_EDGE = 40.0
"""Pixels; an object smaller than this in a photo shows the model nothing."""
CLOSE_UP_EDGE = 512
LOOK_WORKERS = 8
MAX_ANSWER_TOKENS = 200
PROMPT_VERSION = "second-look-3"
UNCLEAR = "unclear"
"""The model's answer when the close-ups show nothing it can name; the photos' name is kept."""
MODEL_ENV = "DISCOVERY_SECOND_LOOK_MODEL"
"""A stronger vision model for the few close-ups a scan needs; the detector's model when unset."""

INSTRUCTION = (
    "You are shown close-up photos of one object in a shop, restaurant or office, cut from a phone walk-through. "
    "Every photo shows the same object in its middle. Say what it is, as the short lowercase name a shop owner "
    "would use, for example 'hand sanitizer dispenser', 'card reader', 'napkin dispenser', 'bar stool', "
    "'menu sign'. If the photos do not clearly show one object you can name, answer 'unclear'. "
    "Set movable to true when one person could pick it up and set it down elsewhere."
)

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "movable"],
    "properties": {"name": {"type": "string"}, "movable": {"type": "boolean"}},
}


@dataclass(frozen=True)
class Photos:
    """What a second look needs to cut close-ups: the cameras, their files, and which way up each was held."""

    cameras: list[PhotoCamera]
    paths: dict[str, pathlib.Path]
    orientations: dict[str, str]


def undecided(object_: DiscoveredObject) -> bool:
    """Whether the photos never agreed what this is: its leading name holds under three quarters of the support."""
    return name_support(object_.weights) < SPLIT


def second_look(
    objects: list[DiscoveredObject],
    photos: Photos,
    *,
    transport: detect.Transport | None = None,
    cache_dir: pathlib.Path | None = None,
) -> list[DiscoveredObject]:
    """Every object, those the photos never agreed on renamed by one look at their best close-ups.

    One the look could not settle keeps the photos' name but is marked unsettled, so the room
    says it needs another look and production asks about it instead of reporting it.
    """
    asking = [index for index, object_ in enumerate(objects) if undecided(object_)]
    if not asking:
        return objects
    renamed = list(objects)
    with concurrent.futures.ThreadPoolExecutor(max_workers=LOOK_WORKERS) as pool:
        answers = pool.map(lambda index: _looked_at(objects[index], photos, transport, cache_dir), asking)
        for index, answer in zip(asking, answers):
            renamed[index] = answer if answer is not None else replace(objects[index], name_settled=False)
    log.info("second look at %d of %d objects", len(asking), len(objects))
    return renamed


def _looked_at(
    object_: DiscoveredObject, photos: Photos, transport: detect.Transport | None, cache_dir: pathlib.Path | None,
) -> DiscoveredObject | None:
    close_ups = _close_ups(object_, photos)
    if not close_ups:
        return None
    try:
        answer = _answer(close_ups, transport, cache_dir)
    except detect.DetectionError as error:
        log.warning("second look at %s failed, keeping the photos' name: %s", object_.name, error)
        return None
    name = answer["name"].strip().lower()
    return replace(object_, name=name, movable=bool(answer["movable"])) if _accepted(object_, name) else None


def _accepted(object_: DiscoveredObject, name: str) -> bool:
    """Whether the answer may replace the photos' name: never a new work surface, and within its kind when the votes lean."""
    if not name or name == UNCLEAR or (is_work_surface(name) and not is_work_surface(object_.name)):
        return False
    if name_support(object_.weights) < OPEN:
        return True
    return _kind(name) in {_kind(voted) for voted in object_.weights}


def _kind(name: str) -> str:
    """The head noun: 'napkin dispenser' and 'sanitizer dispenser' are both dispensers."""
    words = name.strip().lower().split()
    return words[-1] if words else ""


def _close_ups(object_: DiscoveredObject, photos: Photos) -> list[bytes]:
    """The object cut from the photos that saw it largest, stood upright, as JPEG."""
    seen = set(object_.frame_ids)
    framed = [(area, camera, rect) for camera in photos.cameras if camera.frame_id in seen
              for area, rect in [_framed(object_.box, camera)] if rect is not None]
    largest = sorted(framed, key=lambda entry: -entry[0])[:LARGEST_VIEWS]
    cut = [jpeg for _, camera, rect in largest
           if (jpeg := _cut(photos.paths[camera.frame_id], rect, photos.orientations.get(camera.frame_id, "")))]
    return sorted(cut, key=_sharpness, reverse=True)[:CLOSE_UPS]


def _sharpness(jpeg: bytes) -> float:
    """How much fine detail survives: the variance of the image's second derivative. Motion blur flattens it."""
    from PIL import Image

    with Image.open(io.BytesIO(jpeg)) as opened:
        grey = np.asarray(opened.convert("L"), dtype=np.float64)
    if min(grey.shape) < 3:
        return 0.0
    laplacian = (grey[1:-1, :-2] + grey[1:-1, 2:] + grey[:-2, 1:-1] + grey[2:, 1:-1] - 4 * grey[1:-1, 1:-1])
    return float(laplacian.var())


def _framed(box: CarvedBox, camera: PhotoCamera) -> tuple[float, tuple[float, float, float, float] | None]:
    """The box's outline in the photo, padded, and its area; None when it is behind the camera or off the photo."""
    columns, rows, depth = camera.project(_corners(box))
    if (depth <= 0.1).any():
        return 0.0, None
    left, right, top, bottom = columns.min(), columns.max(), rows.min(), rows.max()
    if right < 0 or bottom < 0 or left > camera.width or top > camera.height:
        return 0.0, None
    if min(right - left, bottom - top) < MIN_CLOSE_UP_EDGE:
        return 0.0, None
    pad_x, pad_y = (right - left) * PADDING, (bottom - top) * PADDING
    rect = (max(0.0, left - pad_x), max(0.0, top - pad_y),
            min(float(camera.width), right + pad_x), min(float(camera.height), bottom + pad_y))
    return (rect[2] - rect[0]) * (rect[3] - rect[1]), rect


def _corners(box: CarvedBox) -> np.ndarray:
    cos_t, sin_t = math.cos(box.yaw), math.sin(box.yaw)
    half = np.asarray(box.dimensions, dtype=np.float64) / 2
    corners = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            u, v = sx * half[0], sy * half[1]
            for sz in (-1, 1):
                corners.append((box.centre[0] + u * cos_t - v * sin_t, box.centre[1] + u * sin_t + v * cos_t,
                                box.centre[2] + sz * half[2]))
    return np.asarray(corners)


def _cut(path: pathlib.Path, rect: tuple[float, float, float, float], orientation: str) -> bytes | None:
    from PIL import Image

    try:
        with Image.open(path) as opened:
            left, top, right, bottom = (int(round(value)) for value in rect)
            image = opened.convert("RGB").crop((left, top, right, bottom))
    except (OSError, ValueError) as error:
        log.warning("could not cut a close-up from %s: %s", path.name, error)
        return None
    for _ in range(detect.QUARTER_TURNS_CLOCKWISE.get(orientation, 0)):
        image = image.transpose(Image.Transpose.ROTATE_270)
    image.thumbnail((CLOSE_UP_EDGE, CLOSE_UP_EDGE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def _answer(
    close_ups: list[bytes], transport: detect.Transport | None, cache_dir: pathlib.Path | None,
) -> dict[str, Any]:
    entry = _cache_entry(close_ups, cache_dir)
    if entry is not None and entry.is_file():
        return json.loads(entry.read_text())
    api_key = detect._api_key()
    if transport is None and not api_key:
        raise detect.DetectionAuthError("no key for the vision model, so no second look")
    answer = _parsed(detect._answer(transport, _request_body(close_ups), api_key))
    if entry is not None:
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(json.dumps(answer))
    return answer


def _cache_entry(close_ups: list[bytes], cache_dir: pathlib.Path | None) -> pathlib.Path | None:
    if cache_dir is None:
        return None
    digest = hashlib.sha256()
    for part in (PROMPT_VERSION, _model(), detect.answer_identity()):
        digest.update(part.encode())
    for jpeg in close_ups:
        digest.update(jpeg)
    return cache_dir / "second-look" / f"{digest.hexdigest()}.json"


def _model() -> str:
    return os.environ.get(MODEL_ENV) or detect.answer_model()


def _request_body(close_ups: list[bytes]) -> dict[str, Any]:
    images = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}
              for jpeg in close_ups]
    body: dict[str, Any] = {
        "model": _model(),
        "max_tokens": MAX_ANSWER_TOKENS,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": INSTRUCTION},
            {"role": "user", "content": [{"type": "text", "text": "What is this object?"}, *images]},
        ],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "second_look", "strict": True, "schema": ANSWER_SCHEMA,
        }},
    }
    body.update(detect._request_options())
    return body


def _parsed(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        answer = json.loads(payload["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise detect.DetectionSchemaError(f"the second look came back unreadable: {error}") from error
    if not isinstance(answer, dict) or not isinstance(answer.get("name"), str):
        raise detect.DetectionSchemaError("the second look named nothing")
    return {"name": answer["name"], "movable": bool(answer.get("movable", False))}
