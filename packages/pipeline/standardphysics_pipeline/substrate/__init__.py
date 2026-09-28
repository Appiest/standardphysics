"""The measured world before anybody has named any of it.

Regions come out of the geometry because they have shape, not because a scanner
shipped with a category for them. What they are called is a later question, and
a different layer's.
"""

from .frames import View, frames_seeing, image_of
from .planes import Plane, planes_of
from .regions import (
    Contact,
    Region,
    adjacency,
    area,
    bounds,
    centroid,
    contains,
    corners,
    extent_along,
    footprint,
    footprint_overlap,
    free_space,
    gap,
    gravity,
    hull,
    markings_on,
    overlap_fraction,
    principal_axes,
    region_of,
    regions,
    relative_offset,
    separation,
    volume,
)

__all__ = [
    "Contact",
    "Plane",
    "Region",
    "View",
    "adjacency",
    "area",
    "bounds",
    "centroid",
    "contains",
    "corners",
    "extent_along",
    "footprint",
    "footprint_overlap",
    "frames_seeing",
    "free_space",
    "gap",
    "gravity",
    "hull",
    "image_of",
    "markings_on",
    "overlap_fraction",
    "planes_of",
    "principal_axes",
    "region_of",
    "regions",
    "relative_offset",
    "separation",
    "volume",
]
