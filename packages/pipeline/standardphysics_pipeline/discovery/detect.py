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
never existed. A rate limit is the host saying "later", not "broken": it gets
more attempts, the wait the host asks for, and that wait is shared by every
request in flight so the whole walk backs off together instead of each thread
spending its attempts against the same full budget.

**The model is asked not to reason where the host allows it.** With reasoning
on, the Fireworks detector spent 1,000-2,300 generated tokens and 15-22 s per
photo; off, about 320-500 tokens and 5-6 s. The account's limit is generated
tokens per minute, so this is what lets a whole walk be read in a few minutes.

A request that fails every attempt raises. Nothing here returns an empty list
to mean the network was down: a silent fallback reads downstream as a room
with nothing in it.
"""

from __future__ import annotations

import base64
import json
import pathlib
from dataclasses import dataclass
from typing import Any

from . import taxonomy
from .detection_boxes import map_crop_box_to_sensor, map_crop_point_to_sensor, pixel_box
from .detection_errors import DetectionAuthError, DetectionSchemaError
from .detector_transport import (
    API_KEY_ENV,
    FALLBACK_KEY_ENV,
    Transport,
    answer_model,
    configured_api_key,
    endpoint_host,
    model_answer,
    request_options,
)
from .frame_encoding import EncodedFrame, encode_frame

MAX_OUTPUT_TOKENS = 8_192
MAX_RESPONSE_BYTES = 4_000_000
MAX_DETECTIONS = 40


PERSON_NAMES = taxonomy.names_for(taxonomy.PERSON)

FIXED_NAMES = frozenset({
    "wall", "floor", "ceiling", "window", "door", "doorway", "column",
    "pillar", "staircase", "stairs", "railing", "handrail", "ramp", "ramp landing", "sink", "toilet", "radiator",
    "built-in counter", "built-in shelving", "fireplace",
})

INSTRUCTION = (
    "You look at one photo of a shop or workplace interior and list the objects in it. "
    "Include anything a person uses or that takes up floor or counter space: payment terminals, "
    "card readers, cash drawers, cashier drawers, cash registers, tip screens, self-order kiosks, ordering machines, "
    "touchscreens, menu boards, condiment stations, self-serve stations, napkin, lid and straw dispensers, "
    "pickup counters, handoff shelves, ramps, ramp landings, handrails, railings, steps, thresholds, "
    "monitors, laptops, tablets, printers, phones, kettles, "
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

OUTLET_NAMES = taxonomy.names_for(taxonomy.OUTLET)

CONFUSER_NAMES = taxonomy.names_for(taxonomy.SWITCH) | taxonomy.names_for(taxonomy.SIGN)
"""Names that look like a target but are not one, so nothing is forced into a finding."""


@dataclass(frozen=True)
class ModelRequestInfo:
    """What one real detector request actually was, kept for the evidence trail.

    The provider, the model, the provider's request id, usage and the upright
    orientation the frame was shown in. Never the frame's pixels or a secret.
    """

    frame_id: str
    provider: str
    model: str
    orientation: str
    request_id: str | None = None
    usage: dict[str, int] | None = None


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
    def class_key(self) -> str:
        """The fixed class this finding's free-text name resolves to."""
        if self.category == "outlet":
            return taxonomy.OUTLET
        if self.category == "confuser":
            return taxonomy.classify(self.name) if taxonomy.classify(self.name) in taxonomy.CONFUSER_CLASSES else taxonomy.SIGN
        return taxonomy.classify(self.name)

    @property
    def is_outlet(self) -> bool:
        return self.class_key == taxonomy.OUTLET or self.name.strip().lower() in OUTLET_NAMES

    @property
    def is_confuser(self) -> bool:
        return self.class_key in taxonomy.CONFUSER_CLASSES or self.category == "confuser"

    @property
    def is_television(self) -> bool:
        return self.class_key == taxonomy.TELEVISION

    @property
    def is_service_counter(self) -> bool:
        return self.class_key == taxonomy.SERVICE_COUNTER

    @property
    def is_restroom_entrance(self) -> bool:
        return self.class_key == taxonomy.RESTROOM_ENTRANCE

    @property
    def is_surface_target(self) -> bool:
        """A target that hangs off a measured vertical surface rather than standing on the floor."""
        return self.class_key in taxonomy.SURFACE_TARGET_CLASSES or self.is_outlet

    @property
    def is_attachable_target(self) -> bool:
        """A target the surface-attachment pipeline owns: an outlet or a television.

        Whiteboards are excluded here because their owner is the semantic
        correction pass, which attaches them to walls itself.
        """
        return self.is_outlet or self.class_key == taxonomy.TELEVISION

    @property
    def needs_owner_confirmation(self) -> bool:
        """Counter and restroom candidates are never confirmed by the detector."""
        return self.class_key in taxonomy.OWNER_CONFIRMATION_CLASSES

    @property
    def width(self) -> float:
        return self.box[2] - self.box[0]

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]

    def contains(self, columns, rows):
        left, top, right, bottom = self.box
        return (columns >= left) & (columns <= right) & (rows >= top) & (rows <= bottom)


def detect_objects(
    image_path: pathlib.Path,
    frame_id: str,
    *,
    orientation: str = "landscape_right",
    transport: Transport | None = None,
    recorded: list[ModelRequestInfo] | None = None,
    urgent: bool = True,
) -> list[Detection]:
    """Every object the model finds in one frame, boxed in that frame's stored pixels.

    `urgent` is False only for photos read while their walk is still going on,
    which wait for any request someone is already waiting on (`DetectorSlots`).

    `recorded`, when given, receives one `ModelRequestInfo` per actual provider
    response: provider, model, provider request id, usage and orientation.
    The entry is captured from the response envelope the moment it arrives,
    before any parsing, so a billed response whose content later fails to
    parse still leaves its request metadata in the trail. Entries are only
    appended for real responses: never for cache hits (the caller asks the
    model only on a miss), never for a request that ended without a response.
    """
    frame = encode_frame(image_path, orientation)
    api_key = configured_api_key()
    if transport is None and not api_key:
        raise DetectionAuthError(f"neither {API_KEY_ENV} nor {FALLBACK_KEY_ENV} (or FIREWORKS_API_KEY for Fireworks) is set, so no frame can be read")
    payload = model_answer(transport, _request_body(frame), api_key, urgent)
    if recorded is not None:
        recorded.append(_request_info(payload, frame_id, orientation))
    return _detections_from(payload, frame, frame_id)


def _request_info(payload: dict[str, Any], frame_id: str, orientation: str) -> ModelRequestInfo:
    """Provider, model, request id and usage from a real response, without secrets."""
    host = endpoint_host()
    usage = payload.get("usage")
    if isinstance(usage, dict):
        usage = {str(key): int(value) for key, value in usage.items() if isinstance(value, (int, float))}
    else:
        usage = None
    return ModelRequestInfo(
        frame_id=frame_id,
        provider=host,
        model=answer_model(),
        orientation=orientation,
        request_id=payload.get("id"),
        usage=usage,
    )


def _request_body(frame: EncodedFrame) -> dict[str, Any]:
    data_url = "data:image/jpeg;base64," + base64.b64encode(frame.jpeg).decode("ascii")
    body: dict[str, Any] = {
        "model": answer_model(),
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
    body.update(request_options())
    return body


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
    confidence = _finite_confidence(item.get("confidence", 1.0))
    if confidence is None:
        return None
    raw_box = item.get("box_2d")
    if crop_box is not None:
        box = map_crop_box_to_sensor(raw_box, crop_box, frame.turns)
    else:
        box = pixel_box(raw_box, frame)
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
        confidence=confidence,
        category=category,
        crop_box=crop_box,
        sockets=sockets,
        review_status=review_status,
    )


def _finite_confidence(value: Any) -> float | None:
    """A confidence the model actually gave, clipped to [0, 1], or nothing.

    NaN and infinity are rejected rather than silently clamped: a missing
    number must never become a confident detection.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return min(1.0, max(0.0, number))
