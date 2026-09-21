"""Finding the photograph that shows a patch, and cutting that patch out of it.

A patch is a rectangle in the room. This works out which frame saw it squarely
enough to be worth reading, and returns the pixels. A patch nothing photographed
comes back as nothing, which is how a surface ends up counted as unseen rather
than assumed empty.
"""

from __future__ import annotations

import io
import pathlib
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from ..textures.camera import PhotoCamera
from .faces import Patch

OFF_PATCH = (16, 16, 16)
"""What the parts of the crop that are not on the patch are painted.

Flat and dark, so there is nothing there to mistake for the thing being counted.
"""

NEAREST_METRES = 0.35
"""Closer than this and the phone was against the surface, out of focus."""

FURTHEST_METRES = 4.0
"""Further than this and a spine is a few pixels wide."""

SQUARE_ENOUGH = 0.35
"""How square on the view has to be, as the cosine between the patch's normal and
the line of sight. A patch read at a glancing angle is mostly the side of things
rather than their faces."""

SMALLEST_CROP_PIXELS = 120
"""Below this the crop is too coarse to count, however well placed the camera."""

QUARTER_TURN = -90
"""The phone writes these frames on their side, and a sideways shelf is a hard
read for anything that has seen a library before."""


@dataclass(frozen=True)
class View:
    """One patch as it appears in one frame."""

    patch: Patch
    frame_id: str
    box: tuple[int, int, int, int]
    distance: float
    outline: tuple[tuple[float, float], ...] = ()
    """The patch's four corners in the frame, before the box was squared off.

    A rectangle in the room is a trapezium in a photograph, and the box around a
    trapezium seen at a shallow angle holds a great deal that is not on the patch.
    A floor patch read this way counts the shelves standing behind it.
    """

    @property
    def pixels(self) -> int:
        left, top, right, bottom = self.box
        return (right - left) * (bottom - top)


CANDIDATES = 4
"""How many frames of one patch are worth keeping.

The largest view of a patch is often the one with something standing in front of
it, so the depth test needs somewhere to fall back to. More than a handful buys
nothing, since a walk only passes a given patch once.
"""


def best_view(patch: Patch, cameras: list[PhotoCamera]) -> View | None:
    """The frame that shows this patch largest, among those that show it at all."""
    found = candidates(patch, cameras, limit=1)
    return found[0] if found else None


def candidates(patch: Patch, cameras: list[PhotoCamera], limit: int = CANDIDATES) -> list[View]:
    """The frames that show this patch, largest first."""
    seen = [view for view in (_view(patch, camera) for camera in cameras) if view]
    return sorted(seen, key=lambda view: view.pixels, reverse=True)[:limit]


def _view(patch: Patch, camera: PhotoCamera) -> View | None:
    columns, rows, depth = camera.project(patch.corners)
    if not _in_front(depth) or not _inside(columns, rows, camera):
        return None
    distance = float(np.mean(depth))
    if not _square_enough(patch, camera):
        return None
    box = _box(columns, rows)
    if min(box[2] - box[0], box[3] - box[1]) < SMALLEST_CROP_PIXELS:
        return None
    return View(
        patch=patch,
        frame_id=camera.frame_id,
        box=box,
        distance=distance,
        outline=tuple(zip(columns.tolist(), rows.tolist())),
    )


def _in_front(depth: np.ndarray) -> bool:
    return bool((depth > NEAREST_METRES).all() and (depth < FURTHEST_METRES).all())


def _inside(columns: np.ndarray, rows: np.ndarray, camera: PhotoCamera) -> bool:
    return bool(
        (columns >= 0).all()
        and (columns < camera.width).all()
        and (rows >= 0).all()
        and (rows < camera.height).all()
    )


def _square_enough(patch: Patch, camera: PhotoCamera) -> bool:
    """Whether the camera is in front of the patch and looking at it squarely.

    The normal points out of the region, so a camera facing the patch sees it
    against the line of sight. A positive reading means the camera is round the
    back, looking at this side through the unit, and sees nothing that is on it.
    """
    middle = patch.corners.mean(axis=0)
    line_of_sight = middle - camera.position
    length = float(np.linalg.norm(line_of_sight))
    if not length:
        return False
    return float(np.dot(patch.normal, line_of_sight / length)) <= -SQUARE_ENOUGH


def _box(columns: np.ndarray, rows: np.ndarray) -> tuple[int, int, int, int]:
    return (
        int(np.floor(columns.min())),
        int(np.floor(rows.min())),
        int(np.ceil(columns.max())),
        int(np.ceil(rows.max())),
    )


def cut_out(view: View, frames: pathlib.Path) -> bytes:
    """The patch as a JPEG, turned upright, with everything off it painted out.

    What is left is the surface itself and nothing standing behind it, so a count
    of the crop is a count of the patch.
    """
    with Image.open(frames / view.frame_id) as frame:
        crop = frame.crop(view.box).convert("RGB")
    masked = _only_the_patch(crop, view)
    buffer = io.BytesIO()
    masked.rotate(QUARTER_TURN, expand=True).save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def _only_the_patch(crop: Image.Image, view: View) -> Image.Image:
    if len(view.outline) != 4:
        return crop
    left, top = view.box[0], view.box[1]
    corners = [(column - left, row - top) for column, row in view.outline]
    mask = Image.new("L", crop.size, 0)
    ImageDraw.Draw(mask).polygon(corners, fill=255)
    return Image.composite(crop, Image.new("RGB", crop.size, OFF_PATCH), mask)
