"""Box arithmetic for detections: model boxes back into sensor pixels, padded crops, tiles, and overlap."""

from __future__ import annotations

from typing import Any

from .frame_encoding import EncodedFrame

BOX_SCALE = 1000.0
MIN_BOX_FRACTION = 0.0015
"""A box thinner than this share of the frame carries too few mesh points to fit."""


def pixel_box(values: Any, frame: EncodedFrame) -> tuple[float, float, float, float] | None:
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
) -> tuple[float, float] | None:
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
