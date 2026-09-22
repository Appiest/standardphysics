"""What the model already said about a photo, kept so it is never asked twice.

Reading a whole walk is a few hundred requests. Nothing about a stored photo
changes, so asking again on every rebuild spends real money to receive the same
answer, and a scan reprocessed three times costs three times as much for
nothing.

Entries are keyed by the photo's own bytes, the model that read them, and which
way up it was shown, so a different model, a re-shot frame, or a correction to
how the picture is turned all ask afresh while everything else is read from
disk. The cache never invents an answer: a miss simply asks.
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
from dataclasses import asdict

from .detect import Detection

CACHE_VERSION = "2"


class DetectionCache:
    """One directory of answers, one file per photo."""

    def __init__(self, directory: pathlib.Path, model: str) -> None:
        self.directory = pathlib.Path(directory)
        self.model = model

    def _entry(self, image_path: pathlib.Path, orientation: str) -> pathlib.Path | None:
        try:
            digest = hashlib.sha256(pathlib.Path(image_path).read_bytes()).hexdigest()
        except OSError:
            return None
        key = hashlib.sha256(
            f"{CACHE_VERSION}|{self.model}|{orientation}|{digest}".encode()
        ).hexdigest()
        return self.directory / f"{key}.json"

    def get(self, image_path: pathlib.Path, frame_id: str, orientation: str = "") -> list[Detection] | None:
        entry = self._entry(image_path, orientation)
        if entry is None or not entry.is_file():
            return None
        try:
            stored = json.loads(entry.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(stored, list):
            return None
        """A partially readable entry is a miss, not a smaller answer: the model is asked again."""
        valid = [detection for item in stored if (detection := _detection(item, frame_id)) is not None]
        return valid if len(valid) == len(stored) else None

    def put(self, image_path: pathlib.Path, detections: list[Detection], orientation: str = "") -> None:
        entry = self._entry(image_path, orientation)
        if entry is None:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            entry.write_text(json.dumps([asdict(one) for one in detections]))
        except OSError:
            pass


def _detection(item: dict, frame_id: str) -> Detection | None:
    """One stored answer, or nothing when it is malformed, NaN or infinite.

    A cache entry that cannot be read exactly is not repaired and is not
    silently shrunk: the caller treats the whole entry as a miss.
    """
    if not isinstance(item, dict):
        return None
    name, box = item.get("name"), item.get("box")
    if not isinstance(name, str):
        return None
    box_values = _finite_numbers(box, 4)
    if box_values is None:
        return None
    confidence_values = _finite_numbers([item.get("confidence")], 1)
    if confidence_values is None:
        return None
    crop_box = _finite_box(item.get("crop_box"))
    sockets = _finite_sockets(item.get("sockets"))
    if sockets is None:
        return None
    left, top, right, bottom = box_values
    return Detection(
        frame_id=frame_id,
        name=name,
        box=(left, top, right, bottom),
        movable=bool(item.get("movable", True)),
        confidence=min(1.0, max(0.0, confidence_values[0])),
        category=item.get("category", "object"),
        crop_box=crop_box,
        sockets=sockets,
        review_status=item.get("review_status", "detected"),
        uncertainty_reasons=tuple(item.get("uncertainty_reasons", ()) or ()),
    )


def _finite_numbers(value: object, length: int) -> tuple[float, ...] | None:
    """A sequence of `length` finite numbers, or nothing."""
    if not isinstance(value, (list, tuple)) or len(value) != length:
        return None
    try:
        numbers = tuple(float(one) for one in value)
    except (TypeError, ValueError):
        return None
    return numbers if all(math.isfinite(one) for one in numbers) else None


def _finite_box(value: object) -> tuple[float, float, float, float] | None:
    """A stored crop box with four finite coordinates, or nothing."""
    if not value:
        return None
    numbers = _finite_numbers(value, 4)
    return None if numbers is None else (numbers[0], numbers[1], numbers[2], numbers[3])


def _finite_sockets(value: object) -> tuple[tuple[float, float], ...] | None:
    """Every stored socket point with two finite coordinates, or nothing."""
    if not value:
        return ()
    if not isinstance(value, (list, tuple)):
        return None
    sockets = []
    for socket in value:
        numbers = _finite_numbers(socket, 2)
        if numbers is None:
            return None
        sockets.append((numbers[0], numbers[1]))
    return tuple(sockets)
