"""Whether the camera could actually see the patch, or something stood in the way.

Cutting a crop to a patch's own outline is not enough. A patch of floor behind a
bookcase projects onto the pixels the bookcase occupies, so the crop is correctly
shaped and shows the wrong surface entirely. The same thing happens to a wall with
shelving in front of it, which is how a wall comes to have books on it.

The scan already knows what the camera hit, because the LiDAR recorded it. If the
nearest measured surface along those rays is well in front of the patch, the patch
was behind something and was not seen.
"""

from __future__ import annotations

import numpy as np

from ..textures.camera import PhotoCamera
from .views import View

IN_FRONT_METRES = 0.2
"""How much closer the measured surface has to be before the patch counts as hidden.

Loose enough to absorb the error in a phone's mesh, tight enough to catch a
bookcase standing between the camera and a wall.
"""

ENOUGH_POINTS = 12
"""Below this the mesh says too little about the patch to overrule the geometry."""

HIDDEN_SHARE = 0.5
"""How much of the patch has to be behind something before the whole view goes.

Half, because an edge clipped by a shelf upright is still a readable patch and a
patch mostly behind a bookcase is not.
"""


def seen(views: list[View], cameras: list[PhotoCamera], cloud: np.ndarray) -> list[View]:
    """Only the views where the measured surface really is the patch."""
    by_frame = {camera.frame_id: camera for camera in cameras}
    grouped: dict[str, list[View]] = {}
    for view in views:
        grouped.setdefault(view.frame_id, []).append(view)
    return [
        view
        for frame_id, found in grouped.items()
        if frame_id in by_frame
        for view in _unhidden(found, by_frame[frame_id], cloud)
    ]


def _unhidden(views: list[View], camera: PhotoCamera, cloud: np.ndarray) -> list[View]:
    """One projection of the whole cloud answers for every patch in this frame."""
    columns, rows, depth = camera.project(cloud)
    ahead = depth > 0
    points = np.stack([columns[ahead], rows[ahead]], axis=1)
    distance = depth[ahead]
    return [view for view in views if not _hidden(view, points, distance)]


def _hidden(view: View, points: np.ndarray, distance: np.ndarray) -> bool:
    if len(view.outline) != 4 or not len(points):
        return False
    inside = _inside_quad(points, np.asarray(view.outline, dtype=np.float64))
    if int(inside.sum()) < ENOUGH_POINTS:
        return False
    in_front = distance[inside] < view.distance - IN_FRONT_METRES
    return bool(in_front.mean() > HIDDEN_SHARE)


def _inside_quad(points: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Which points fall within the patch's outline, by the sign of each edge.

    The outline is a projected rectangle, so it is convex and a point is inside
    when it sits on the same side of all four edges.
    """
    signs = []
    for index in range(4):
        start, end = quad[index], quad[(index + 1) % 4]
        edge = end - start
        offset = points - start
        signs.append(edge[0] * offset[:, 1] - edge[1] * offset[:, 0])
    stacked = np.stack(signs, axis=1)
    return np.all(stacked >= 0, axis=1) | np.all(stacked <= 0, axis=1)
