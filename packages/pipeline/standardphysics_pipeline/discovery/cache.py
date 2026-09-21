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
import pathlib
from dataclasses import asdict

from .detect import Detection

CACHE_VERSION = "1"


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
        return [_detection(item, frame_id) for item in stored]

    def put(self, image_path: pathlib.Path, detections: list[Detection], orientation: str = "") -> None:
        entry = self._entry(image_path, orientation)
        if entry is None:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            entry.write_text(json.dumps([asdict(one) for one in detections]))
        except OSError:
            pass


def _detection(item: dict, frame_id: str) -> Detection:
    return Detection(
        frame_id=frame_id,
        name=item["name"],
        box=tuple(item["box"]),
        movable=bool(item["movable"]),
        confidence=float(item["confidence"]),
    )
