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

OUTLET_NAMES = frozenset({
    "outlet", "electrical outlet", "power outlet", "wall outlet",
    "receptacle", "electrical receptacle", "power strip", "extension lead",
    "socket", "plug socket", "duplex outlet",
})

CONFUSER_NAMES = frozenset({
    "switch", "light switch", "data port", "ethernet port", "network port",
    "cable plate", "blank plate", "phone jack", "coaxial port",
})

Transport = Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]]


class DetectionError(RuntimeError):
    """The frame could not be read, or the model did not answer."""


class DetectionAuthError(DetectionError):
    """Authentication or authorization failure (401, 403, missing key). Never retried."""


class DetectionSchemaError(DetectionError):
    """Malformed schema or unreadable model response. Never retried."""


class DetectionTransientError(DetectionError):
    """Rate limit (429), server error (500/502/503/504), network timeout. Retried with bounded backoff."""


@dataclass(frozen=True)
class Detection:
    frame_id: str
    name: str
    box: tuple[float, float, float, float]
    """left, top, right, bottom in stored sensor pixels."""
    movable: bool
    confidence: float
    category: str = "object"
    crop_box: tuple[float, float, float, float] | None = None
    sockets: tuple[tuple[float, float], ...] = ()
    review_status: str = "detected"
    uncertainty_reasons: tuple[str, ...] = ()

    @property
    def is_person(self) -> bool:
        return self.name.strip().lower() in PERSON_NAMES

    @property
    def is_outlet(self) -> bool:
        return self.name.strip().lower() in OUTLET_NAMES or self.category == "outlet"

    @property
    def is_confuser(self) -> bool:
        return self.name.strip().lower() in CONFUSER_NAMES or self.category == "confuser"

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
        raise DetectionAuthError(f"neither {API_KEY_ENV} nor {FALLBACK_KEY_ENV} is set, so no frame can be read")
    body = _request_body(frame)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _detections_from(_post(transport, body, api_key), frame, frame_id)
        except (DetectionAuthError, DetectionSchemaError):
            raise
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
    except DetectionError:
        raise
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise DetectionAuthError(f"the vision model rejected authentication: HTTP {error.code}") from error
        if error.code in (400, 422):
            raise DetectionSchemaError(f"the vision model rejected request schema: HTTP {error.code}") from error
        raise DetectionTransientError(f"the vision model had a transient HTTP error: HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise DetectionTransientError(f"the vision model timed out or network failed: {error}") from error
    except (OSError, ValueError) as error:
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
        raise DetectionSchemaError(f"the vision model returned no message: {error}") from error
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    try:
        objects = json.loads(content)["objects"]
    except (ValueError, KeyError, TypeError) as error:
        raise DetectionSchemaError(f"the vision model returned unreadable objects: {error}") from error
    return objects if isinstance(objects, list) else []


def _one_detection(
    item: dict[str, Any],
    frame: EncodedFrame,
    frame_id: str,
    *,
    crop_box: tuple[float, float, float, float] | None = None,
) -> Detection | None:
    name = str(item.get("name", "")).strip().lower()
    raw_box = item.get("box_2d")
    if crop_box is not None:
        box = map_crop_box_to_sensor(raw_box, crop_box, frame.turns)
    else:
        box = _pixel_box(raw_box, frame)
    if not name or box is None:
        return None
    category = "outlet" if name in OUTLET_NAMES else ("confuser" if name in CONFUSER_NAMES else "object")
    review_status = "detected" if category != "confuser" else "rejected_confuser"
    sockets: tuple[tuple[float, float], ...] = ()
    if "sockets" in item and isinstance(item["sockets"], (list, tuple)):
        parsed_sockets = []
        for s in item["sockets"]:
            if isinstance(s, (list, tuple)) and len(s) == 2:
                sock_pt = map_crop_point_to_sensor(
                    s,
                    crop_box or (0.0, 0.0, float(frame.width), float(frame.height)),
                    frame.turns,
                )
                if sock_pt is not None:
                    parsed_sockets.append(sock_pt)
        sockets = tuple(parsed_sockets)
    return Detection(
        frame_id=frame_id,
        name=name,
        box=box,
        movable=bool(item.get("movable", True)) and name not in FIXED_NAMES,
        confidence=_clamped(item.get("confidence", 1.0)),
        category=category,
        crop_box=crop_box,
        sockets=sockets,
        review_status=review_status,
    )


def _pixel_box(values: Any, frame: EncodedFrame) -> tuple[float, float, float, float] | None:
    """A model box, in the picture it saw, turned back into stored sensor pixels."""
    return map_crop_box_to_sensor(values, (0.0, 0.0, float(frame.width), float(frame.height)), frame.turns)


def map_crop_box_to_sensor(
    values: Any,
    crop_box: tuple[float, float, float, float],
    turns: int,
) -> tuple[float, float, float, float] | None:
    """A model box [ymin, xmin, ymax, xmax] in upright crop/image space turned back into sensor pixels."""
    import math

    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return None
    try:
        top, left, bottom, right = (float(value) / BOX_SCALE for value in values)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(top) and math.isfinite(left) and math.isfinite(bottom) and math.isfinite(right)):
        return None
    top, bottom = sorted((_clamped(top), _clamped(bottom)))
    left, right = sorted((_clamped(left), _clamped(right)))
    if (right - left) < MIN_BOX_FRACTION or (bottom - top) < MIN_BOX_FRACTION:
        return None
    for _ in range(turns):
        left, top, right, bottom = top, 1.0 - right, bottom, 1.0 - left
    c_left, c_top, c_right, c_bottom = crop_box
    c_w = c_right - c_left
    c_h = c_bottom - c_top
    return (
        c_left + left * c_w,
        c_top + top * c_h,
        c_left + right * c_w,
        c_top + bottom * c_h,
    )


def map_crop_point_to_sensor(
    point: Any,
    crop_box: tuple[float, float, float, float],
    turns: int,
) -> tuple[float, float, float] | None:
    """A model point [y, x] in [0, 1000] upright crop space turned back into sensor (x, y) pixels."""
    import math

    if not isinstance(point, (list, tuple)) or len(point) != 2:
        return None
    try:
        y_val, x_val = (float(v) / BOX_SCALE for v in point)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(x_val) and math.isfinite(y_val)):
        return None
    x_val = _clamped(x_val)
    y_val = _clamped(y_val)
    for _ in range(turns):
        x_val, y_val = y_val, 1.0 - x_val
    c_left, c_top, c_right, c_bottom = crop_box
    return (
        c_left + x_val * (c_right - c_left),
        c_top + y_val * (c_bottom - c_top),
    )


def extract_padded_crop(
    image: Any,
    box: tuple[float, float, float, float],
    padding_fraction: float = 0.20,
) -> tuple[Any, tuple[float, float, float, float]]:
    """Pads a sensor box by padding_fraction, clips to image bounds, and returns (cropped_image, crop_box)."""
    b_left, b_top, b_right, b_bottom = box
    pad_x = (b_right - b_left) * padding_fraction
    pad_y = (b_bottom - b_top) * padding_fraction
    c_left = max(0.0, b_left - pad_x)
    c_top = max(0.0, b_top - pad_y)
    c_right = min(float(image.width), b_right + pad_x)
    c_bottom = min(float(image.height), b_bottom + pad_y)
    crop_rect = (c_left, c_top, c_right, c_bottom)
    cropped = image.crop((int(round(c_left)), int(round(c_top)), int(round(c_right)), int(round(c_bottom))))
    return cropped, crop_rect


def generate_tiles(
    image_width: int,
    image_height: int,
    tile_size: tuple[int, int] = (1024, 1024),
    overlap: float = 0.20,
) -> list[tuple[float, float, float, float]]:
    """Generates overlapping tile rectangles (left, top, right, bottom) covering the image."""
    tw, th = tile_size
    if image_width <= tw and image_height <= th:
        return [(0.0, 0.0, float(image_width), float(image_height))]
    step_x = max(1, int(tw * (1.0 - overlap)))
    step_y = max(1, int(th * (1.0 - overlap)))
    tiles = []
    y = 0
    while y < image_height:
        top = y
        bottom = min(y + th, image_height)
        if bottom == image_height and top > 0:
            top = max(0, bottom - th)
        x = 0
        while x < image_width:
            left = x
            right = min(x + tw, image_width)
            if right == image_width and left > 0:
                left = max(0, right - tw)
            tile = (float(left), float(top), float(right), float(bottom))
            if tile not in tiles:
                tiles.append(tile)
            if right >= image_width:
                break
            x += step_x
        if bottom >= image_height:
            break
        y += step_y
    return tiles


def box_iou(b1: tuple[float, float, float, float], b2: tuple[float, float, float, float]) -> float:
    """Intersection over union between two 2D boxes (left, top, right, bottom)."""
    x1 = max(b1[0], b2[0])
    y1 = max(b1[1], b2[1])
    x2 = min(b1[2], b2[2])
    y2 = min(b1[3], b2[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area1 = max(0.0, b1[2] - b1[0]) * max(0.0, b1[3] - b1[1])
    area2 = max(0.0, b2[2] - b2[0]) * max(0.0, b2[3] - b2[1])
    union = area1 + area2 - intersection
    return intersection / union if union > 0 else 0.0


def _clamped(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
