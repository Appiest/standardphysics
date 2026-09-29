"""Turning a stored camera frame into the upright, size-capped JPEG the vision model is shown."""

from __future__ import annotations

import io
import pathlib
from dataclasses import dataclass

from .detection_errors import DetectionError

MAX_IMAGE_EDGE = 1024
MAX_IMAGE_BYTES = 3_000_000
MAX_SOURCE_BYTES = 16_000_000
MAX_SOURCE_PIXELS = 40_000_000
JPEG_QUALITIES = (85, 75, 65, 55)


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
