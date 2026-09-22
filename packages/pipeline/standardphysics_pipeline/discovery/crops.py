"""Source-resolution photographic crops that evidence a detection.

A crop is cut from the frame exactly as the camera stored it, at the stored
resolution, and padded a little so a person (or a second, finer model pass)
can look at the actual outlet-sized region instead of a thumbnail of the whole
room. The crop rectangle is the detection's sensor box, so whatever box the
crop later produces maps back onto the same sensor pixels through the recorded
offset.

Crops are deterministic: the same frame and box always produce the same crop
id and the same bytes, so replaying discovery never accumulates copies.
"""

from __future__ import annotations

import hashlib
import pathlib

from PIL import Image

from .detect import extract_padded_crop

CROP_PADDING = 0.20


def crop_id_for(frame_id: str, box: tuple[float, float, float, float]) -> str:
    """The stable name a crop of this box in this frame is stored under."""
    digest = hashlib.sha256(
        ",".join(f"{value:.1f}" for value in box).encode()
    ).hexdigest()[:16]
    return f"{frame_id}-{digest}"


def save_crop(
    image_path: pathlib.Path,
    frame_id: str,
    box: tuple[float, float, float, float],
    crop_dir: pathlib.Path,
    *,
    padding_fraction: float = CROP_PADDING,
) -> str | None:
    """Writes a source-resolution padded crop for a sensor box and returns its crop id.

    Returns None when the frame cannot be read or the box is degenerate, and
    never writes a partial file: the crop either lands whole or the caller
    reports the observation without a resolvable reference.
    """
    left, top, right, bottom = box
    if not (right > left and bottom > top):
        return None
    try:
        with Image.open(image_path) as opened:
            image = opened.convert("RGB")
    except (OSError, ValueError):
        return None
    cropped, crop_box = extract_padded_crop(image, box, padding_fraction=padding_fraction)
    crop_id = crop_id_for(frame_id, box)
    try:
        crop_dir.mkdir(parents=True, exist_ok=True)
        path = crop_dir / f"{crop_id}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(path, format="JPEG", quality=90)
    except OSError:
        return None
    return crop_id


def crop_box_of(
    image_path: pathlib.Path,
    box: tuple[float, float, float, float],
    *,
    padding_fraction: float = CROP_PADDING,
) -> tuple[float, float, float, float] | None:
    """The padded, clipped sensor rectangle a crop of this box covers, or None.

    This is the offset every later box inside the crop is mapped back through.
    """
    left, top, right, bottom = box
    if not (right > left and bottom > top):
        return None
    try:
        with Image.open(image_path) as opened:
            width, height = opened.size
    except (OSError, ValueError):
        return None
    pad_x = (right - left) * padding_fraction
    pad_y = (bottom - top) * padding_fraction
    return (
        max(0.0, left - pad_x),
        max(0.0, top - pad_y),
        min(float(width), right + pad_x),
        min(float(height), bottom + pad_y),
    )
