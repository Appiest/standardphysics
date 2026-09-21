"""What a vision model sees in one captured frame, in that frame's own pixels.

RoomPlan only boxes the furniture categories Apple ships, so a payment
terminal, a monitor or a laptop reaches us as unclaimed LiDAR and nothing
else. This asks a grounding model what is in the picture and where, and the
answer is a pixel rectangle we can turn back into mesh points.

**The model is shown the room the way up a person saw it.** Frames are stored
exactly as the camera delivered them, which on a phone held upright is on its
side. A detector shown a sideways photo reads a person on a sofa as lying down
and the laptop on their knees as a chair or a box. So the image is turned
upright for the model using the interface orientation the phone recorded.

**Boxes come back in sensor pixels.** The pose intrinsics describe the stored
sensor grid, so every rectangle is turned back into it before anything projects
through it. The turn is undone exactly once, here, and nothing downstream
knows the photo was ever rotated.

**Boxes arrive as Gemini writes them**: `[ymin, xmin, ymax, xmax]`, each value
0-1000 of the image's height or width. Asking for the convention the model was
trained on gets better corners than asking it to translate.

A frame is worth asking about more than once. Reading a whole walk means
hundreds of requests in a couple of minutes, and at that rate a few come back
rate-limited or with a truncated body. Those are retried with a widening,
jittered wait, because one frame lost to a blip is an object that silently
never existed.

A request that fails every attempt raises. Nothing here returns an empty list
to mean the network was down: a silent fallback reads downstream as a room
with nothing in it.
"""

from __future__ import annotations

import base64
import io
import json
import os
import pathlib
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

MODEL_ENV = "DISCOVERY_MODEL"
API_KEY_ENV = "DISCOVERY_API_KEY"
BASE_URL_ENV = "DISCOVERY_BASE_URL"
FALLBACK_KEY_ENV = "OPENROUTER_API_KEY"
FALLBACK_BASE_URL_ENV = "OPENROUTER_BASE_URL"
DEFAULT_MODEL = "google/gemini-3.8-flash"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_HOST = "openrouter.ai"
PROVIDER_ROUTING = {"data_collection": "deny"}
"""OpenRouter's own routing rules. Every other host rejects or ignores them, so
they travel only when the request is going to OpenRouter."""
REQUEST_TIMEOUT_SECONDS = 120.0
MAX_ATTEMPTS = 4
FIRST_BACKOFF_SECONDS = 1.5
BACKOFF_GROWTH = 2.5
MAX_OUTPUT_TOKENS = 8_192
MAX_RESPONSE_BYTES = 4_000_000
MAX_DETECTIONS = 40

MAX_IMAGE_EDGE = 1024
MAX_IMAGE_BYTES = 3_000_000
MAX_SOURCE_BYTES = 16_000_000
MAX_SOURCE_PIXELS = 40_000_000
JPEG_QUALITIES = (85, 75, 65, 55)

BOX_SCALE = 1000.0
MIN_BOX_FRACTION = 0.0015
"""A box thinner than this share of the frame carries too few mesh points to fit."""

PERSON_NAMES = frozenset({
    "person", "people", "human", "man", "woman", "child", "customer",
    "shopper", "employee", "staff", "worker", "hand", "arm", "leg", "face",
})

FIXED_NAMES = frozenset({
    "wall", "floor", "ceiling", "window", "door", "doorway", "column",
    "pillar", "staircase", "stairs", "railing", "sink", "toilet", "radiator",
    "built-in counter", "built-in shelving", "fireplace",
})

INSTRUCTION = (
    "You look at one photo of a shop or workplace interior and list the objects in it. "
    "Include anything a person uses or that takes up floor or counter space: payment terminals, "
    "card readers, cash registers, monitors, laptops, tablets, printers, phones, kettles, "
    "espresso machines, blenders, microwaves, refrigerators, display cases, shelving, signage, "
    "boxes, bins, chairs, stools, tables, counters, planters, fans, speakers, lamps. "
    "Also list every person you see, named exactly 'person'. "
    "Give each object a short lowercase name a shop owner would use. "
    "Set movable to true when one or two people could pick the object up and set it down "
    "somewhere else, and false when it is built in, plumbed in, or too heavy to move. "
    "Box every object separately: a laptop sitting on a desk is its own object, not part of the desk. "
    "Report the box as [ymin, xmin, ymax, xmax] with each number between 0 and 1000, "
    "measured against the image height for y and the image width for x."
)

DETECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["objects"],
    "properties": {
        "objects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "box_2d", "movable", "confidence"],
                "properties": {
                    "name": {"type": "string"},
                    "box_2d": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                    "movable": {"type": "boolean"},
                    "confidence": {"type": "number"},
                },
            },
        }
    },
}

Transport = Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]]


class DetectionError(RuntimeError):
    """The frame could not be read, or the model did not answer."""


@dataclass(frozen=True)
class Detection:
    frame_id: str
    name: str
    box: tuple[float, float, float, float]
    """left, top, right, bottom in stored sensor pixels."""
    movable: bool
    confidence: float

    @property
    def is_person(self) -> bool:
        return self.name.strip().lower() in PERSON_NAMES

    @property
    def width(self) -> float:
        return self.box[2] - self.box[0]

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]

    def contains(self, columns, rows):
        left, top, right, bottom = self.box
        return (columns >= left) & (columns <= right) & (rows >= top) & (rows <= bottom)


QUARTER_TURNS_CLOCKWISE = {
    "portrait": 1,
    "portrait_upside_down": 3,
    "landscape_left": 2,
    "landscape_right": 0,
}
"""How far the stored sensor image turns clockwise to stand the room upright.

`landscape_right` is how the sensor is mounted, so it needs no turn at all.
An orientation we do not recognise is left alone rather than guessed at.
"""


@dataclass(frozen=True)
class EncodedFrame:
    jpeg: bytes
    width: int
    height: int
    """The stored sensor resolution the boxes are turned back into."""
    turns: int = 0
    """Quarter turns clockwise applied before the model saw it."""


def detect_objects(
    image_path: pathlib.Path,
    frame_id: str,
    *,
    orientation: str = "landscape_right",
    transport: Transport | None = None,
) -> list[Detection]:
    """Every object the model finds in one frame, boxed in that frame's stored pixels."""
    frame = encode_frame(image_path, orientation)
    api_key = _api_key()
    if transport is None and not api_key:
        raise DetectionError(f"neither {API_KEY_ENV} nor {FALLBACK_KEY_ENV} is set, so no frame can be read")
    body = _request_body(frame)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _detections_from(_post(transport, body, api_key), frame, frame_id)
        except DetectionError:
            if attempt == MAX_ATTEMPTS:
                raise
            time.sleep(_backoff(attempt))
    raise DetectionError("unreachable")


def _backoff(attempt: int) -> float:
    """A widening wait, spread out so retries from many frames do not land together."""
    return FIRST_BACKOFF_SECONDS * (BACKOFF_GROWTH ** (attempt - 1)) * (0.5 + random.random())


def encode_frame(image_path: pathlib.Path, orientation: str = "landscape_right") -> EncodedFrame:
    """The frame as JPEG under the size cap, stood upright, with its sensor resolution kept."""
    from PIL import Image

    source = pathlib.Path(image_path)
    try:
        if source.stat().st_size > MAX_SOURCE_BYTES:
            raise DetectionError(f"{source.name} is too large to read")
        with Image.open(source) as opened:
            if opened.width * opened.height > MAX_SOURCE_PIXELS:
                raise DetectionError(f"{source.name} has too many pixels to read")
            image = opened.convert("RGB")
    except (OSError, ValueError) as error:
        raise DetectionError(f"unreadable frame {source.name}: {error}") from error
    stored_width, stored_height = image.width, image.height
    turns = QUARTER_TURNS_CLOCKWISE.get(orientation, 0)
    for _ in range(turns):
        image = image.transpose(Image.Transpose.ROTATE_270)
    image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
    return EncodedFrame(_compressed(image), stored_width, stored_height, turns)


def _compressed(image) -> bytes:
    encoded = b""
    for quality in JPEG_QUALITIES:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        encoded = buffer.getvalue()
        if len(encoded) <= MAX_IMAGE_BYTES:
            return encoded
    raise DetectionError("frame will not compress under the size limit")


def _api_key() -> str:
    return os.environ.get(API_KEY_ENV) or os.environ.get(FALLBACK_KEY_ENV) or ""


def _base_url() -> str:
    configured = os.environ.get(BASE_URL_ENV) or os.environ.get(FALLBACK_BASE_URL_ENV)
    return (configured or DEFAULT_BASE_URL).rstrip("/")


def _request_body(frame: EncodedFrame) -> dict[str, Any]:
    data_url = "data:image/jpeg;base64," + base64.b64encode(frame.jpeg).decode("ascii")
    body: dict[str, Any] = {
        "model": os.environ.get(MODEL_ENV) or DEFAULT_MODEL,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": [
            {"role": "system", "content": INSTRUCTION},
            {"role": "user", "content": [
                {"type": "text", "text": "List every object in this photo with its box."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "detections", "strict": True, "schema": DETECTION_SCHEMA,
        }},
    }
    if OPENROUTER_HOST in _base_url():
        body["provider"] = PROVIDER_ROUTING
    return body


def _post(transport: Transport | None, body: dict[str, Any], api_key: str) -> dict[str, Any]:
    url = f"{_base_url()}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        return (transport or _openrouter_post)(url, body, headers)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as error:
        raise DetectionError(f"the vision model did not answer: {error}") from error


def _openrouter_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read(MAX_RESPONSE_BYTES))


def _detections_from(payload: dict[str, Any], frame: EncodedFrame, frame_id: str) -> list[Detection]:
    objects = _objects_in(payload)
    detections = []
    for item in objects[:MAX_DETECTIONS]:
        detection = _one_detection(item, frame, frame_id)
        if detection is not None:
            detections.append(detection)
    return detections


def _objects_in(payload: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise DetectionError(f"the vision model returned no message: {error}") from error
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    try:
        objects = json.loads(content)["objects"]
    except (ValueError, KeyError, TypeError) as error:
        raise DetectionError(f"the vision model returned unreadable objects: {error}") from error
    return objects if isinstance(objects, list) else []


def _one_detection(item: dict[str, Any], frame: EncodedFrame, frame_id: str) -> Detection | None:
    name = str(item.get("name", "")).strip().lower()
    box = _pixel_box(item.get("box_2d"), frame)
    if not name or box is None:
        return None
    return Detection(
        frame_id=frame_id,
        name=name,
        box=box,
        movable=bool(item.get("movable", True)) and name not in FIXED_NAMES,
        confidence=_clamped(item.get("confidence", 1.0)),
    )


def _pixel_box(values: Any, frame: EncodedFrame) -> tuple[float, float, float, float] | None:
    """A model box, in the picture it saw, turned back into stored sensor pixels."""
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return None
    try:
        top, left, bottom, right = (float(value) / BOX_SCALE for value in values)
    except (TypeError, ValueError):
        return None
    top, bottom = sorted((_clamped(top), _clamped(bottom)))
    left, right = sorted((_clamped(left), _clamped(right)))
    if (right - left) < MIN_BOX_FRACTION or (bottom - top) < MIN_BOX_FRACTION:
        return None
    for _ in range(frame.turns):
        left, top, right, bottom = top, 1.0 - right, bottom, 1.0 - left
    return (
        left * frame.width, top * frame.height,
        right * frame.width, bottom * frame.height,
    )


def _clamped(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
