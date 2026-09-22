"""Photographic UVs on captured surfaces, with triangle-based visibility.

This optional offline baker requires the pipeline's reconstruction extra. It
keeps unseen triangles neutral and never generates missing photographic detail.
"""
from __future__ import annotations

import numpy as np
from numba import njit

from .camera import PhotoCamera


@njit(cache=True)
def raster_depth(u, v, depth, triangles, width, height):
    """Perspective-correct depth at sensor pixel centres, without depth erosion."""
    buffer = np.full((height, width), np.inf, dtype=np.float32)
    for face in triangles:
        a, b, c = face
        if min(depth[a], depth[b], depth[c]) <= .15:
            continue
        x0, x1 = max(0, int(np.floor(min(u[a], u[b], u[c])))), min(width-1, int(np.ceil(max(u[a], u[b], u[c]))))
        y0, y1 = max(0, int(np.floor(min(v[a], v[b], v[c])))), min(height-1, int(np.ceil(max(v[a], v[b], v[c]))))
        determinant = (v[b]-v[c])*(u[a]-u[c])+(u[c]-u[b])*(v[a]-v[c])
        if x0 > x1 or y0 > y1 or abs(determinant) < 1e-10:
            continue
        for y in range(y0, y1+1):
            for x in range(x0, x1+1):
                wa = ((v[b]-v[c])*(x-u[c])+(u[c]-u[b])*(y-v[c]))/determinant
                wb = ((v[c]-v[a])*(x-u[c])+(u[a]-u[c])*(y-v[c]))/determinant
                wc = 1-wa-wb
                if min(wa, wb, wc) < -1e-6:
                    continue
                inverse = wa/depth[a]+wb/depth[b]+wc/depth[c]
                if inverse > 0:
                    buffer[y, x] = min(buffer[y, x], 1/inverse)
    return buffer


def surface_depth(camera: PhotoCamera, vertices: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    u, v, depth = camera.project(vertices)
    return raster_depth(u, v, depth, triangles, camera.width, camera.height)


def visible_points(camera: PhotoCamera, points: np.ndarray, buffer: np.ndarray, tolerance: float = .05):
    """Require a nearby visible surface; never fill unobserved holes with photos."""
    u, v, depth = camera.project(points)
    inside = (depth > .15) & (u >= 1) & (u < camera.width-2) & (v >= 1) & (v < camera.height-2)
    x = np.clip(np.floor(u).astype(np.int64), 0, camera.width-2)
    y = np.clip(np.floor(v).astype(np.int64), 0, camera.height-2)
    samples = np.stack([buffer[y, x], buffer[y, x+1], buffer[y+1, x], buffer[y+1, x+1]])
    # The closest of four neighbors conservatively protects foreground edges.
    nearest = samples.min(axis=0)
    return inside & np.isfinite(nearest) & (np.abs(depth-nearest) <= tolerance), u, v


def choose_views_partial(vertices, triangles, cameras, depth_vertices=None, depth_triangles=None, on_progress=None, min_facing=0.2, centre_required=True):
    """Partial-support photograph selection.

    Unlike ``choose_views`` this does not demand every corner of a face be
    visible. A face is eligible when its centre is visible AND at least
    ``MIN_SAMPLES`` of a fixed 7-point pattern (3 corners, 3 edge midpoints,
    centre) passes the depth check, so partially occluded surfaces keep their
    photograph instead of dropping to neutral. Unknown depth is never treated
    as visible. Faces no photograph reaches remain unassigned.
    """
    corners = vertices[triangles]
    centres = corners.mean(axis=1)
    mids = (corners + np.roll(corners, 2, axis=1)) / 2
    samples = np.concatenate([corners, mids, centres[:, None, :]], axis=1)
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    areas = np.linalg.norm(cross, axis=1) / 2
    normals = cross / np.maximum(2 * areas[:, None], 1e-12)
    best = np.zeros(len(triangles), dtype=np.float32)
    assignment = np.full(len(triangles), -1, dtype=np.int32)
    depth_vertices = vertices if depth_vertices is None else depth_vertices
    depth_triangles = triangles if depth_triangles is None else depth_triangles
    for index, camera in enumerate(cameras):
        buffer = surface_depth(camera, depth_vertices, depth_triangles)
        visible, _, _ = visible_points(camera, samples.reshape(-1, 3), buffer)
        face_support = visible.reshape(len(triangles), SAMPLES).sum(axis=1)
        centre_visible, _, _ = visible_points(camera, centres, buffer)
        corner_u, corner_v, _ = camera.project(corners.reshape(-1, 3))
        corner_u = corner_u.reshape(len(triangles), 3)
        corner_v = corner_v.reshape(len(triangles), 3)
        border_raw = np.minimum.reduce([corner_u, corner_v,
                                        camera.width - 1 - corner_u, camera.height - 1 - corner_v])
        border = np.clip(border_raw.min(axis=1) / 16, 0, 1)
        toward = camera.position - centres
        distance = np.linalg.norm(toward, axis=1)
        facing = (normals * toward).sum(axis=1) / np.maximum(distance, 1e-9)
        corner_visible = visible.reshape(len(triangles), SAMPLES)[:, :3].sum(axis=1) >= 2
        mid_visible = visible.reshape(len(triangles), SAMPLES)[:, 3:6].sum(axis=1) >= 2
        spread = corner_visible & mid_visible
        if centre_required:
            accepted = (face_support >= MIN_SAMPLES) & centre_visible & spread & (facing > min_facing) & (border > 0.02)
        else:
            accepted = (face_support >= MIN_SAMPLES) & spread & (facing > min_facing) & (border > 0.02)
        score = np.where(accepted, facing ** 2 / np.maximum(distance, 0.5) * border * face_support / SAMPLES, 0)
        better = score > best
        assignment[better] = index
        best[better] = score[better]
        if on_progress:
            on_progress(index + 1, len(cameras), float(areas[assignment >= 0].sum() / max(areas.sum(), 1e-9)))
    return assignment, areas


SAMPLES = 7
MIN_SAMPLES = 5

def region_weighted_decimate(vertices, triangles, region_mask, region_budget, rest_budget):
    """Decimate measured geometry with separate budgets inside and outside a region.

    Display-mesh simplification that collapses detail uniformly can erase a
    wall segment entirely (diagnosis v-diag-001: the Moffett pilot wall vanished
    from a 250k-face display mesh, leaving the far surface rendered neutral).
    This keeps ``region_budget`` faces inside ``region_mask`` and ``rest_budget``
    outside; both parts snap back onto the measured surface later in the bake.
    Returns (display_vertices, display_triangles).
    """
    import trimesh

    if not region_mask.any():
        raise ValueError("region mask selects no faces")
    if region_budget < 4 or rest_budget < 4:
        raise ValueError("budgets must be at least 4 faces")
    parts = []
    for label, mask, budget in (("region", region_mask, region_budget),
                                ("rest", ~region_mask, rest_budget)):
        part = trimesh.Trimesh(vertices, triangles[mask], process=False)
        reduced = part.simplify_quadric_decimation(face_count=min(budget, len(part.faces)))
        parts.append((np.asarray(reduced.vertices), np.asarray(reduced.faces, dtype=np.int64)))
    display_vertices = np.concatenate([part[0] for part in parts], axis=0)
    offsets = np.concatenate([[0], np.cumsum([len(part[0]) for part in parts[:-1]])])
    display_triangles = np.concatenate(
        [np.asarray(part[1]) + int(offset) for part, offset in zip(parts, offsets)], axis=0)
    return display_vertices, display_triangles


def snap_to_measured(display_vertices, full_vertices, max_distance=0.15):
    """Pull simplified display vertices back onto the measured surface.

    Quadric decimation leaves the display mesh centimetres off the measured
    surface (and photos project through the display positions, doubling the
    visual error). Each display vertex moves to its nearest measured-surface
    vertex when that neighbour is within ``max_distance``; vertices beyond the
    cap keep their decimated position, so missing geometry is never invented.
    """
    from scipy.spatial import cKDTree

    tree = cKDTree(full_vertices)
    distance, index = tree.query(display_vertices, distance_upper_bound=max_distance)
    mask = np.isfinite(distance)
    snapped = np.array(display_vertices)
    snapped[mask] = full_vertices[index[mask]]
    farthest = float(distance[mask].max()) if mask.any() else 0.0
    return snapped, int(mask.sum()), farthest


def choose_views(vertices, triangles, cameras, depth_vertices=None, depth_triangles=None, on_progress=None):
    """One photograph per face; all three corners and centre must be visible."""
    corners = vertices[triangles]
    centres = corners.mean(axis=1)
    cross = np.cross(corners[:, 1]-corners[:, 0], corners[:, 2]-corners[:, 0])
    areas = np.linalg.norm(cross, axis=1)/2
    normals = cross/np.maximum(2*areas[:, None], 1e-12)
    best = np.zeros(len(triangles), dtype=np.float32)
    assignment = np.full(len(triangles), -1, dtype=np.int32)
    depth_vertices = vertices if depth_vertices is None else depth_vertices
    depth_triangles = triangles if depth_triangles is None else depth_triangles
    for index, camera in enumerate(cameras):
        buffer = surface_depth(camera, depth_vertices, depth_triangles)
        visible, u, v = visible_points(camera, vertices, buffer)
        centre_visible, _, _ = visible_points(camera, centres, buffer)
        toward = camera.position-centres
        distance = np.linalg.norm(toward, axis=1)
        facing = (normals*toward).sum(axis=1)/np.maximum(distance, 1e-9)
        border = np.minimum.reduce([u, v, camera.width-1-u, camera.height-1-v])
        border_weight = np.clip(border[triangles].min(axis=1)/16, 0, 1)
        accepted = visible[triangles].all(axis=1) & centre_visible & (facing > .2)
        score = np.where(accepted, facing**2/np.maximum(distance, .5)*border_weight, 0)
        better = score > best
        assignment[better] = index
        best[better] = score[better]
        if on_progress:
            on_progress(index+1, len(cameras), float(areas[assignment >= 0].sum()/max(areas.sum(), 1e-9)))
    return assignment, areas
