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
