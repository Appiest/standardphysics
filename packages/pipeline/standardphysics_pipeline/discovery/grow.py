"""Growing a piece of something to the whole thing it is a piece of.

A detector draws a rectangle round what it can see, and a big thing is seldom
all in one picture. Moffitt's service desk is four metres long and was named
once, by a rectangle that ran off the edge of the photo, so its carved box was
a fourteen-inch slice of one end. Merging cannot help: the other views of the
desk were never named as anything.

The mesh knows where the desk ends. Take away the room's own sheets, and the
broad level bands the floor and ceiling make, and what is left falls apart
into separate pieces that each stand on their own: the desk, each security
gate, the barrier. A piece of something named belongs to one of those, and the
thing is the whole of it.

    regions    the leftover mesh split into pieces that touch nothing else
    claims     each discovered object belongs to the piece most of it lies in
    groups     objects in one piece that could be one thing become one thing
    growth     the largest group in a piece takes the whole piece's box

Two objects are one thing when their names agree and no photo drew them apart.
Anything else in the piece, a monitor on the desk, keeps its own box. A piece
larger than any furniture is a chain of things touching, and nothing grows
into it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace

import numpy as np
from scipy.spatial import cKDTree
from standardphysics_contracts import SceneGraph, bounds_the_room

from ..textures.camera import PhotoCamera
from .boxes import inside
from .carve import MAX_EXTENT, CarvedBox, fit_box, unoccluded
from .clusters import voxel_components
from .merge import DiscoveredObject, _names_agree, _share_inside

SHEET_SKIN = 0.06
"""Points this close to a wall or other room-bounding sheet are the sheet."""
LEVEL_BIN = 0.02
LEVEL_SHARE = 0.03
"""A height band holding this share of the leftover points is a broad level surface, not an object."""
LEVEL_SKIN = 0.06
"""Points this close to the lowest or highest broad level surface belong to it."""
OVERHEAD = 2.0
"""How far above the floor a broad level surface must be to be overhead rather than a tabletop or a platform."""
MIN_REGION_POINTS = 60
TOUCHING = 0.06
"""An object's point lies in a region when a region point is this close to it."""
BELONGS = 0.5
"""Share of an object's points that must lie in one region for the object to be part of it."""
MAIN_THING = 0.2
"""Share of a region a group must already cover to take the whole of it.

A card reader named on a desk nobody named would otherwise become the desk.
Coverage is measured in space, not by counting points, because an object seen
in fifteen photos carries fifteen copies of the same few points."""
SEEN_SHARE = 0.15
"""A photo saw a region when this share of its points is visible. A big thing is seldom more than a quarter visible from one place: its far side is hidden and its ends run out of the picture."""
NEAR_LIMIT = 0.05
SEEN_SAMPLE = 500
"""Points tested per photo when asking whether it saw a region; the answer is a share, so a sample settles it."""


PART_OF_IT = 0.7
"""Share of another object's box inside the grown box for it to be part of the grown thing."""
NOT_AN_ITEM_ON_IT = 0.2
"""Share of the grown box's volume that object must reach too, or it is something standing on the grown thing."""


@dataclass(frozen=True)
class Regions:
    points: np.ndarray
    labels: np.ndarray
    """A region number per point; regions too small to be anything are -1."""
    floor: float | None = None
    """The top of the lowest broad level surface, where the points of anything standing on it were cut off."""

    def members(self, region: int) -> np.ndarray:
        return self.points[self.labels == region]


def regions_of(points: np.ndarray, graph: SceneGraph) -> Regions:
    """The leftover mesh split into pieces, with the room's sheets and broad level surfaces taken out."""
    points = np.asarray(points, dtype=np.float64)
    low, high = _level_surfaces(points[:, 2])
    kept = points[~_on_sheets(points, graph) & _between(points[:, 2], low, high)]
    labels = voxel_components(kept) if len(kept) else np.zeros(0, dtype=np.int64)
    if len(labels):
        counts = np.bincount(labels)
        labels = np.where(counts[labels] >= MIN_REGION_POINTS, labels, -1)
    return Regions(kept, labels, low)


def _on_sheets(points: np.ndarray, graph: SceneGraph) -> np.ndarray:
    on_sheet = np.zeros(len(points), dtype=bool)
    for node in graph.nodes:
        if bounds_the_room(node):
            on_sheet |= inside(points, node, SHEET_SKIN)
    return on_sheet


def _level_surfaces(heights: np.ndarray) -> tuple[float | None, float | None]:
    """The top of the lowest and the bottom of the highest height band holding a large share of the mesh.

    RoomPlan measures walls and a floor outline but no ceiling, and its floor
    misses the scanned floor by more than a claim margin in places. Both leave
    broad level sheets of points behind that would join everything standing on
    or hanging under them into one piece.

    A band only counts as the floor at the very bottom of the points, and as
    overhead well above head height. A platform or a run of tabletops is also a
    broad level band, and cutting one away would cut the thing itself.
    """
    if not len(heights):
        return None, None
    counts, edges = np.histogram(heights, bins=np.arange(heights.min(), heights.max() + LEVEL_BIN, LEVEL_BIN))
    broad = edges[np.flatnonzero(counts >= LEVEL_SHARE * len(heights))]
    bottom = float(np.percentile(heights, 2))
    low = float(broad[0] + LEVEL_BIN) if len(broad) and broad[0] <= bottom + 2 * LEVEL_SKIN else None
    base = bottom if low is None else low
    high = float(broad[-1]) if len(broad) and broad[-1] >= base + OVERHEAD else None
    return low, high


def _between(heights: np.ndarray, low: float | None, high: float | None) -> np.ndarray:
    keep = np.ones(len(heights), dtype=bool)
    if low is not None:
        keep &= heights > low + LEVEL_SKIN
    if high is not None:
        keep &= heights < high - LEVEL_SKIN
    return keep


def grown(objects: list[DiscoveredObject], regions: Regions) -> list[tuple[DiscoveredObject, bool]]:
    """Every object, grown to its whole region where it is the region's main thing, and whether it grew."""
    if not len(regions.points) or not objects:
        return [(object_, False) for object_ in objects]
    tree = cKDTree(regions.points)
    home = [_region_of(object_, regions, tree) for object_ in objects]
    by_region: dict[int, list[int]] = {}
    for index, region in enumerate(home):
        if region is not None:
            by_region.setdefault(region, []).append(index)
    results: list[tuple[DiscoveredObject, bool] | None] = [(object_, False) for object_ in objects]
    for region, members in by_region.items():
        _grow_region(objects, members, regions.members(region), regions.floor, results)
    return [result for result in results if result is not None]


def _region_of(object_: DiscoveredObject, regions: Regions, tree: cKDTree) -> int | None:
    distance, nearest = tree.query(object_.box.points, distance_upper_bound=TOUCHING)
    found = regions.labels[nearest[np.isfinite(distance)]]
    found = found[found >= 0]
    if not len(found):
        return None
    region, count = Counter(found.tolist()).most_common(1)[0]
    return region if count >= BELONGS * len(object_.box.points) else None


def _grow_region(
    objects: list[DiscoveredObject],
    members: list[int],
    region_points: np.ndarray,
    floor: float | None,
    results: list[tuple[DiscoveredObject, bool] | None],
) -> None:
    """The region's largest group of one thing becomes the whole region.

    Anything else in the region that fills a large part of the grown box is
    another name for part of the same thing and joins it. The rest, the things
    standing on it, keep their own boxes.
    """
    box = fit_box(region_points)
    if box is None or max(box.dimensions[:2]) > MAX_EXTENT:
        return
    group = max(_groups(objects, members), key=lambda indices: sum(len(objects[i].box.points) for i in indices))
    if _covered_share(region_points, [objects[i] for i in group]) < MAIN_THING:
        return
    box = _down_to(box, floor)
    joined = group + [i for i in members if i not in group and _part_of(objects[i].box, box)]
    results[joined[0]] = (_as_one([objects[i] for i in joined], box), True)
    for index in joined[1:]:
        results[index] = None


def _down_to(box: CarvedBox, floor: float | None) -> CarvedBox:
    """The box reaching the floor again, when its foot is where the floor's points were cut away."""
    if floor is None:
        return box
    foot = box.centre[2] - box.dimensions[2] / 2
    if not floor < foot <= floor + 2 * LEVEL_SKIN + LEVEL_BIN:
        return box
    height = box.dimensions[2] + foot - floor
    return replace(
        box,
        centre=(box.centre[0], box.centre[1], floor + height / 2),
        dimensions=(box.dimensions[0], box.dimensions[1], height),
    )


def _part_of(inner: CarvedBox, grown_box: CarvedBox) -> bool:
    return (
        _share_inside(inner, grown_box) >= PART_OF_IT
        and inner.volume >= NOT_AN_ITEM_ON_IT * grown_box.volume
    )


def _covered_share(region_points: np.ndarray, group: list[DiscoveredObject]) -> float:
    """How much of the region lies within touching distance of what the group already holds."""
    held = cKDTree(np.concatenate([object_.box.points for object_ in group]))
    distance, _ = held.query(region_points, distance_upper_bound=TOUCHING)
    return float(np.isfinite(distance).mean())


def _groups(objects: list[DiscoveredObject], members: list[int]) -> list[list[int]]:
    """Objects that could be one thing: names that agree, and no photo that drew them apart."""
    groups: list[list[int]] = []
    for index in sorted(members, key=lambda i: -len(objects[i].box.points)):
        home = next((group for group in groups if all(_could_be_one(objects[index], objects[j]) for j in group)), None)
        if home is None:
            groups.append([index])
        else:
            home.append(index)
    return groups


def _could_be_one(first: DiscoveredObject, second: DiscoveredObject) -> bool:
    return _names_agree(first.name, second.name) and not set(first.frame_ids) & set(second.frame_ids)


def _as_one(group: list[DiscoveredObject], box: CarvedBox) -> DiscoveredObject:
    weights: Counter[str] = Counter()
    for object_ in group:
        weights += object_.weights
    for_it = sum(object_.movable_votes[0] for object_ in group)
    total = sum(object_.movable_votes[1] for object_ in group)
    return DiscoveredObject(
        name=weights.most_common(1)[0][0],
        box=box,
        movable=for_it * 2 >= total,
        confidence=max(object_.confidence for object_ in group),
        frame_ids=tuple(sorted({frame for object_ in group for frame in object_.frame_ids})),
        weights=weights,
        movable_votes=(for_it, total),
    )


def seen_from(points: np.ndarray, cameras: list[PhotoCamera], buffers: dict[str, np.ndarray]) -> list[np.ndarray]:
    """Where each photo that saw these points was taken from.

    A grown object was named from as little as one photo, but its box comes
    from the mesh, and the mesh is measured from every place that saw it.
    """
    points = points[np.linspace(0, len(points) - 1, min(len(points), SEEN_SAMPLE)).astype(int)]
    positions = []
    for camera in cameras:
        columns, rows, depth = camera.project(points)
        visible = (depth > NEAR_LIMIT) & (columns >= 0) & (columns < camera.width) & (rows >= 0) & (rows < camera.height)
        buffer = buffers.get(camera.frame_id)
        if buffer is not None:
            visible &= unoccluded(columns, rows, depth, camera, buffer)
        if visible.mean() >= SEEN_SHARE:
            positions.append(np.asarray(camera.position))
    return positions
