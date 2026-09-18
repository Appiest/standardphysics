"""How many of something is on the surfaces of a scanned room.

The division of labour is the whole point. A model looks at one patch of measured
size and says how many things it can see in it. Every metre, every area and every
multiplication belongs to the engine, so no figure in the answer came out of a
sentence.

That also means nothing here asks what kind of region it is looking at. Every
region with real extent gets patches, every patch gets counted, and a desk comes
back as zero because a model looking at a desk sees none of what was asked for.
A wall is measured the same way as anything else, so shelving the scanner called
a wall is still read, and a bare wall costs a few readings and returns nothing.
The floor drops out on its own, having no height to carry a face.

What was never photographed is reported as never photographed. An estimate over
the surfaces a walk actually covered is an answer; the same number presented as a
total for the room is a guess wearing an answer's clothes.
"""

from __future__ import annotations

import pathlib
import statistics
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
from standardphysics_contracts import SceneGraph

from ..textures.camera import PhotoCamera
from .depth import seen
from .faces import PATCH_METRES, Face, Patch, faces_of, patches_on
from .views import View, best_view, candidates, cut_out

Counter = Callable[[bytes, str], int | None]
"""Given a patch of image and what is being looked for, how many are visible."""

READINGS = 3
"""How many times one patch is read. The spike showed a tight crop repeating to
within a few percent, and a median of three keeps one bad read from carrying."""

ENOUGH_SEEN = 0.6
"""How much of a face has to have been photographed before the rest is inferred.

A walk down one aisle sees the part of a wall the shelving stands against and
almost none of the rest. Scaling the books found in five square metres up to
twenty-nine assumes the unseen part looks like the seen part, and the seen part
was chosen by where the books were. Below this, what was counted is the answer
and the gap is reported as a gap.
"""


@dataclass(frozen=True)
class Reading:
    """One patch, counted."""

    view: View
    counts: list[int]

    @property
    def count(self) -> float:
        return statistics.median(self.counts)

    @property
    def per_square_metre(self) -> float:
        return self.count / self.view.patch.area

    @property
    def agreement(self) -> float:
        """Largest reading over smallest, as a plain measure of how steady it was."""
        low, high = min(self.counts), max(self.counts)
        return high / low if low else 1.0 if high == 0 else float("inf")


@dataclass(frozen=True)
class Surface:
    """One face of one region, and what was found on the part of it that was seen."""

    face: Face
    readings: list[Reading] = field(default_factory=list)
    patches: int = 0

    @property
    def seen_area(self) -> float:
        return sum(reading.view.patch.area for reading in self.readings)

    @property
    def density(self) -> float:
        """What was counted, over the area it was counted on.

        The median of the patches was wrong here and wrong in a way that hid
        things: most of a floor holds nothing, so the middle patch holds nothing,
        and a surface with hundreds of things counted on it scaled to zero. A
        surface is not uniform, and the total over the part that was seen is what
        scales honestly to the part that was not.
        """
        return self.counted / self.seen_area if self.seen_area else 0.0

    @property
    def coverage(self) -> float:
        return self.seen_area / self.face.area if self.face.area else 0.0

    @property
    def inferred(self) -> bool:
        """Whether enough of this face was seen to say anything about the rest."""
        return self.coverage >= ENOUGH_SEEN

    @property
    def estimate(self) -> float:
        """The density that was measured, over the whole face it was measured on.

        Only where most of the face was actually photographed. Everywhere else
        this is what was counted, because the unseen part of a face is unknown
        rather than more of the same.
        """
        if not self.readings:
            return 0.0
        return self.density * self.face.area if self.inferred else self.counted

    @property
    def counted(self) -> float:
        """What was actually seen, before any scaling."""
        return sum(reading.count for reading in self.readings)


@dataclass(frozen=True)
class Tally:
    thing: str
    surfaces: list[Surface]

    @property
    def found(self) -> list[Surface]:
        return [surface for surface in self.surfaces if surface.counted > 0]

    @property
    def counted(self) -> float:
        """The ones actually seen in a photograph, with nothing scaled."""
        return sum(surface.counted for surface in self.found)

    @property
    def estimate(self) -> float:
        return sum(surface.estimate for surface in self.found)

    @property
    def seen_area(self) -> float:
        return sum(surface.seen_area for surface in self.surfaces)

    @property
    def surface_area(self) -> float:
        return sum(surface.face.area for surface in self.surfaces)

    @property
    def coverage(self) -> float:
        return self.seen_area / self.surface_area if self.surface_area else 0.0


def tally(
    graph: SceneGraph,
    cameras: list[PhotoCamera],
    frames: pathlib.Path,
    thing: str,
    counter: Counter,
    *,
    size: float = PATCH_METRES,
    readings: int = READINGS,
    workers: int = 8,
    cloud: np.ndarray | None = None,
) -> Tally:
    """Count `thing` across every surface in the room that a frame caught.

    Without `cloud` there is no way to tell a patch from whatever stood in front
    of it, and a floor behind a bookcase is counted as holding the bookcase's
    contents. Pass the scan's measured points whenever there are any.
    """
    plans = [
        (face, patches_on(node, face, size))
        for node in graph.nodes
        for face in faces_of(node)
    ]
    views = {
        _key(face): _views_for(patches, cameras, cloud) for face, patches in plans
    }
    read = _read_all(views, frames, thing, counter, readings, workers)
    return Tally(
        thing=thing,
        surfaces=[
            Surface(face=face, readings=read.get(_key(face), []), patches=len(patches))
            for face, patches in plans
        ],
    )


def _key(face: Face) -> tuple[str, int]:
    """A face is one side of one region, and no two share that."""
    return (face.node_id, face.side)


def _views_for(
    patches: list[Patch], cameras: list[PhotoCamera], cloud: np.ndarray | None
) -> list[View]:
    """The best unobstructed view of each patch, where there is one."""
    if cloud is None:
        return [view for view in (best_view(patch, cameras) for patch in patches) if view]
    return [
        found[0]
        for found in (
            seen(candidates(patch, cameras), cameras, cloud) for patch in patches
        )
        if found
    ]


def _read_all(
    views: dict[tuple[str, int], list[View]],
    frames: pathlib.Path,
    thing: str,
    counter: Counter,
    readings: int,
    workers: int,
) -> dict[tuple[str, int], list[Reading]]:
    work = [(key, view) for key, found in views.items() for view in found]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        done = pool.map(lambda item: _read(item, frames, thing, counter, readings), work)
    out: dict[tuple[str, int], list[Reading]] = {}
    for key, reading in done:
        if reading is not None:
            out.setdefault(key, []).append(reading)
    return out


def _read(
    item: tuple[tuple[str, int], View],
    frames: pathlib.Path,
    thing: str,
    counter: Counter,
    readings: int,
) -> tuple[tuple[str, int], Reading | None]:
    key, view = item
    try:
        jpeg = cut_out(view, frames)
    except (OSError, ValueError):
        return key, None
    counts = [count for count in (counter(jpeg, thing) for _ in range(readings)) if count is not None]
    return (key, Reading(view=view, counts=counts)) if counts else (key, None)


def report(result: Tally) -> str:
    """What was counted, what it scales to, and what was never looked at."""
    lines = [
        f"{result.thing} on the surfaces of this scan",
        f"  counted in a photograph  {result.counted:.0f}",
        f"  best estimate            {result.estimate:.0f}",
        f"  surface photographed     {result.coverage:.0%}"
        f" ({result.seen_area:.1f} of {result.surface_area:.1f} square metres)",
        "",
    ]
    lines.extend(_row(surface) for surface in sorted(result.found, key=lambda s: -s.estimate))
    if result.coverage < 1:
        lines.append("")
        lines.append(
            f"  {1 - result.coverage:.0%} of the surface in this room was never photographed,"
            " and nothing above says what is on it."
        )
    return "\n".join(lines)


def _row(surface: Surface) -> str:
    how = "scaled to the face" if surface.inferred else "counted only, too little seen"
    return (
        f"  {surface.face.name:22} {surface.estimate:6.0f}"
        f"  {surface.counted:5.0f} seen over {surface.seen_area:5.1f} of"
        f" {surface.face.area:5.1f} m2 ({surface.coverage:3.0%})  {how}"
    )
