"""One object, seen from many places, becoming one object.

Walking past a desk puts the laptop on it in eighty frames. Each of those is a
separate rectangle carved into a separate box, and they all overlap because
they are all the same laptop.

Joining happens in two rounds, because names and shapes fail differently.

**By name and place.** Two candidates join when their boxes overlap and their
names agree. Agreement is loose: a couch and a sofa are one thing, and a
detector that alternates between the words should not produce two objects.

**By place alone.** A bag on a chair gets called a bag from one side and a
rucksack from the other, and no shared word saves it. So a second round joins
two objects that occupy each other's space, whatever they were called, and the
name with the most views behind it wins. This round asks for containment in
**both** directions, because one-directional containment is just a small thing
standing on a big one.

Neither round lets joins chain. A candidate joins an object only if it sits
inside that object's own box, so a pillow touching a sofa touching a chair
stays three things instead of collapsing into one piece of furniture.

The merged object is refitted from every point that fed it, which is why a box
built from several viewpoints is tighter than any single view.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from .carve import CarvedBox, fit_box
from .detect import Detection

JOINS_OBJECT = 0.5
"""Share of a candidate's own box that must lie inside an object's for it to be another view of it."""
SAME_THING = 0.7
"""Two objects each this far inside the other are one thing under two names."""
STOPWORDS = frozenset({"a", "an", "the", "of", "on", "small", "large", "black", "white"})
SYNONYMS = {
    "couch": "sofa",
    "settee": "sofa",
    "armchair": "chair",
    "stool": "chair",
    "rucksack": "backpack",
    "notebook computer": "laptop",
    "macbook": "laptop",
    "monitor": "screen",
    "display": "screen",
    "television": "screen",
    "tv": "screen",
    "card reader": "payment terminal",
    "card machine": "payment terminal",
    "pos terminal": "payment terminal",
    "till": "register",
    "cash register": "register",
    "cushion": "pillow",
    "carpet": "rug",
    "mat": "rug",
    "bin": "box",
    "crate": "box",
}


@dataclass(frozen=True)
class Candidate:
    detection: Detection
    box: CarvedBox


@dataclass(frozen=True)
class DiscoveredObject:
    name: str
    box: CarvedBox
    movable: bool
    confidence: float
    frame_ids: tuple[str, ...]
    weights: Counter[str] = field(default_factory=Counter)
    """How much detector confidence stood behind each name proposed for this object."""
    movable_votes: tuple[int, int] = (0, 0)
    """Views calling it movable, and views in total."""

    @property
    def views(self) -> int:
        return len(self.frame_ids)


def merge_candidates(candidates: list[Candidate]) -> list[DiscoveredObject]:
    """Every candidate gathered into the objects they are views of, refitted from all their points."""
    if not candidates:
        return []
    return _fold_duplicates(_gather(candidates))


def _gather(candidates: list[Candidate]) -> list[DiscoveredObject]:
    """Best-supported candidates seed the objects; the rest join one or start their own.

    Working from the largest down means an object is seeded by the view that saw
    most of it, so later fragments are matched against a box worth matching.
    """
    objects: list[DiscoveredObject] = []
    for candidate in sorted(candidates, key=lambda one: -len(one.box.points)):
        home = _where_it_belongs(candidate, objects)
        if home is None:
            objects.append(_first_view(candidate))
        else:
            joined = _joined(objects[home], candidate)
            if joined is not None:
                objects[home] = joined
    return objects


def _where_it_belongs(candidate: Candidate, objects: list[DiscoveredObject]) -> int | None:
    best, best_share = None, JOINS_OBJECT
    for index, object_ in enumerate(objects):
        if not _names_agree(candidate.detection.name, object_.name):
            continue
        share = _share_inside(candidate.box, object_.box)
        if share >= best_share:
            best, best_share = index, share
    return best


def _fold_duplicates(objects: list[DiscoveredObject]) -> list[DiscoveredObject]:
    """Two objects each mostly inside the other are one thing seen under two names."""
    settled: list[DiscoveredObject] = []
    for object_ in sorted(objects, key=lambda one: -one.views):
        twin = next(
            (index for index, other in enumerate(settled)
             if _share_inside(object_.box, other.box) >= SAME_THING
             and _share_inside(other.box, object_.box) >= SAME_THING),
            None,
        )
        if twin is None:
            settled.append(object_)
        else:
            combined = _combined([settled[twin], object_])
            if combined is not None:
                settled[twin] = combined
    return settled


def _share_inside(inner: CarvedBox, outer: CarvedBox) -> float:
    """How much of `inner`'s axis-aligned box lies within `outer`'s."""
    inner_low, inner_high = _bounds(inner)
    outer_low, outer_high = _bounds(outer)
    span = np.maximum(0.0, np.minimum(inner_high, outer_high) - np.maximum(inner_low, outer_low))
    own = float(np.prod(inner_high - inner_low))
    return float(np.prod(span)) / own if own > 0 else 0.0


def _names_agree(first: str, second: str) -> bool:
    left, right = _words(first), _words(second)
    return bool(left and right and (left <= right or right <= left))


def _words(name: str) -> frozenset[str]:
    settled = SYNONYMS.get(name.strip().lower(), name.strip().lower())
    return frozenset(word for word in settled.split() if word not in STOPWORDS)


def _bounds(box: CarvedBox) -> tuple[np.ndarray, np.ndarray]:
    """The axis-aligned range the turned box occupies."""
    half = np.asarray(box.dimensions, dtype=np.float64) / 2
    cos_t, sin_t = abs(np.cos(box.yaw)), abs(np.sin(box.yaw))
    reach = np.asarray([
        half[0] * cos_t + half[1] * sin_t,
        half[0] * sin_t + half[1] * cos_t,
        half[2],
    ])
    centre = np.asarray(box.centre, dtype=np.float64)
    return centre - reach, centre + reach


def _first_view(candidate: Candidate) -> DiscoveredObject:
    return DiscoveredObject(
        name=_settled(candidate.detection.name),
        box=candidate.box,
        movable=candidate.detection.movable,
        confidence=candidate.detection.confidence,
        frame_ids=(candidate.detection.frame_id,),
        weights=Counter({_settled(candidate.detection.name): candidate.detection.confidence}),
        movable_votes=(1 if candidate.detection.movable else 0, 1),
    )


def _joined(object_: DiscoveredObject, candidate: Candidate) -> DiscoveredObject | None:
    """The object with one more view of it folded in and its box refitted."""
    weights = object_.weights + Counter({_settled(candidate.detection.name): candidate.detection.confidence})
    for_it, total = object_.movable_votes
    return _rebuilt(
        [object_.box, candidate.box],
        weights,
        movable_votes=(for_it + (1 if candidate.detection.movable else 0), total + 1),
        confidence=max(object_.confidence, candidate.detection.confidence),
        frame_ids=set(object_.frame_ids) | {candidate.detection.frame_id},
    )


def _combined(group: list[DiscoveredObject]) -> DiscoveredObject | None:
    """One object from two that turned out to be the same thing under two names."""
    weights: Counter[str] = Counter()
    votes = [0, 0]
    for object_ in group:
        weights += object_.weights
        votes[0] += object_.movable_votes[0]
        votes[1] += object_.movable_votes[1]
    return _rebuilt(
        [object_.box for object_ in group],
        weights,
        movable_votes=(votes[0], votes[1]),
        confidence=max(object_.confidence for object_ in group),
        frame_ids={frame_id for object_ in group for frame_id in object_.frame_ids},
    )


def _rebuilt(
    boxes: list[CarvedBox],
    weights: Counter[str],
    *,
    movable_votes: tuple[int, int],
    confidence: float,
    frame_ids: set[str],
) -> DiscoveredObject | None:
    box = fit_box(np.concatenate([one.points for one in boxes], axis=0))
    if box is None:
        return None
    for_it, total = movable_votes
    return DiscoveredObject(
        name=weights.most_common(1)[0][0],
        box=box,
        movable=for_it * 2 >= total,
        confidence=confidence,
        frame_ids=tuple(sorted(frame_ids)),
        weights=weights,
        movable_votes=movable_votes,
    )


def _settled(name: str) -> str:
    return SYNONYMS.get(name.strip().lower(), name.strip().lower())
