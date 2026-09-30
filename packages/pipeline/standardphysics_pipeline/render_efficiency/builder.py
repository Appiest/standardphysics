"""Build deterministic surface Gaussians from the measured mesh and best-view colour.

Centres stay on the measured surface; normals come from measured triangles and
are rejected when degenerate.  Colour is interpolated from per-vertex best-view
colour (see ``textures.scan_colour``) rather than any learned head.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .metrics import MetricError
from .surface_splats import (
    SurfaceGaussians,
    inverse_sigmoid,
    sh0_from_rgb,
    surface_covariance,
)


def sample_mesh(
        vertices: np.ndarray,
        triangles: np.ndarray,
        spacing: float,
        seed: int = 1729,
        max_samples: int | None = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Area-weighted samples on the mesh, with owner triangle and face normal.

        Returns (points, normals, owners, barycentric_a, barycentric_b).  Degenerate
        triangles (``area <= 1e-10``) are skipped and their normals are never
        invented.
        """

        vertices = np.asarray(vertices, dtype=np.float64)
        triangles = np.asarray(triangles, dtype=np.int64)
        if vertices.ndim != 2 or vertices.shape[1] != 3:
            raise MetricError(f"vertices must be Nx3, got {vertices.shape}")
        if triangles.ndim != 2 or triangles.shape[1] != 3:
            raise MetricError(f"triangles must be Tx3, got {triangles.shape}")
        if not np.isfinite(spacing) or spacing <= 0.0:
            raise MetricError(f"spacing must be positive finite, got {spacing}")
        if triangles.size and (
            (triangles < 0).any() or (triangles >= len(vertices)).any()
        ):
            raise MetricError("triangles reference vertices outside the vertex array")

        corners = vertices[triangles]
        edge_1 = corners[:, 1] - corners[:, 0]
        edge_2 = corners[:, 2] - corners[:, 0]
        cross = np.cross(edge_1, edge_2)
        areas = np.linalg.norm(cross, axis=1) / 2.0
        valid = areas > 1e-10
        counts = np.where(valid, np.maximum(1, np.rint(areas / spacing**2)).astype(np.int64), 0)
        if not counts.any():
            raise MetricError("mesh has no non-degenerate triangles")

        generator = np.random.default_rng(seed)
        total = int(counts.sum())
        if max_samples is not None and total > max_samples:
            weights = counts.astype(np.float64)
            weights /= weights.sum()
            owner = generator.choice(np.arange(len(triangles)), size=max_samples, p=weights, replace=True)
        else:
            owner = np.repeat(np.arange(len(triangles)), counts)

        first = generator.random(len(owner))
        second = generator.random(len(owner))
        root = np.sqrt(first)
        bary_a = 1.0 - root
        bary_b = root * (1.0 - second)
        bary_c = root * second
        corners = vertices[triangles[owner]]
        points = bary_a[:, None] * corners[:, 0] + bary_b[:, None] * corners[:, 1] + bary_c[:, None] * corners[:, 2]

        normals = cross[owner] / np.maximum(np.linalg.norm(cross[owner], axis=1, keepdims=True), 1e-12)
        return (
            points.astype(np.float32),
            normals.astype(np.float32),
            owner.astype(np.int64),
            bary_a.astype(np.float32),
            bary_b.astype(np.float32),
        )


@dataclass(frozen=True)
class SampleState:
    """Photo support for one exported sample.

    ``supported`` means every corner of the owning triangle was observed and the
    sample carries its source frame IDs. Unsupported samples are pruned before export.
    """

    triangle: int
    supported: bool
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class BuildResult:
    gaussians: SurfaceGaussians
    supported_count: int
    rejected_count: int
    unsupported_count: int
    sample_states: list[SampleState]


def _check_colour_domain(colours: np.ndarray) -> None:
    if not np.isfinite(colours).all():
        raise MetricError("vertex_colours contains NaN or Inf")
    if (colours < 0.0).any() or (colours > 1.0).any():
        raise MetricError("vertex_colours must lie within [0,1]")


def _validate_triangle_indices(triangle_arr: np.ndarray, vertex_count: int) -> None:
    if triangle_arr.size and (
        (triangle_arr < 0).any() or (triangle_arr >= vertex_count).any()
    ):
        raise MetricError("triangles reference vertices outside the vertex array")


def _sample_support(seen: np.ndarray | None, triangle_arr: np.ndarray, owners: np.ndarray) -> np.ndarray:
    if seen is None:
        return np.ones(len(owners), dtype=bool)
    sampled_verts = triangle_arr[owners]
    return np.asarray(seen, dtype=bool)[sampled_verts].all(axis=1)


def _interpolated_colours(
    vertex_colours: np.ndarray,
    triangle_arr: np.ndarray,
    owners: np.ndarray,
    bary_a: np.ndarray,
    bary_b: np.ndarray,
) -> np.ndarray:
    corner_colours = vertex_colours[triangle_arr[owners]]
    bary_c = (1.0 - bary_a - bary_b).astype(np.float64)
    colours = (
        bary_a[:, None] * corner_colours[:, 0]
        + bary_b[:, None] * corner_colours[:, 1]
        + bary_c[:, None] * corner_colours[:, 2]
    )
    if not np.isfinite(colours).all() or (colours > 1.0).any() or (colours < -1e-6).any():
        raise MetricError("samples produce non-finite or out-of-range colours")
    return colours


def _keep_supported_finite(
    points: np.ndarray,
    normals: np.ndarray,
    colours: np.ndarray,
    owners: np.ndarray,
    per_sample_supported: np.ndarray,
    source_ids: np.ndarray | None,
    triangle_arr: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[SampleState], int]:
    points = points[per_sample_supported]
    normals = normals[per_sample_supported]
    colours = colours[per_sample_supported]
    kept_owners = owners[per_sample_supported]
    kept_sources: list[tuple[str, ...]] | None = None
    if source_ids is not None:
        sampled_ids = np.asarray(source_ids, dtype=object)[triangle_arr[kept_owners]]
        kept_sources = [tuple(sorted(set(row))) for row in sampled_ids.tolist()]

    finite = np.isfinite(points).all(axis=1) & np.isfinite(normals).all(axis=1) & np.isfinite(colours).all(axis=1)
    norm_finite = (np.linalg.norm(normals, axis=1) > 0.5) & finite
    rejected = int((~norm_finite).sum())
    points, normals, colours = points[norm_finite], normals[norm_finite], colours[norm_finite]
    kept_owners = kept_owners[norm_finite]
    if kept_sources is not None:
        kept_sources = [row for row, keep in zip(kept_sources, norm_finite, strict=True) if keep]
    if len(points) == 0:
        raise MetricError("no supported, non-degenerate surface samples remain")
    source_iter = kept_sources if kept_sources is not None else [()] * len(kept_owners)
    states = [
        SampleState(triangle=int(owner), supported=True, source_ids=sources or ())
        for owner, sources in zip(kept_owners, source_iter, strict=True)
    ]
    return points, normals, colours, kept_owners, states, rejected


def build_surface_gaussians(
    vertices: np.ndarray,
    triangles: np.ndarray,
    vertex_colours: np.ndarray,
    spacing: float,
    tangent_factor: float = 0.75,
    normal_ratio: float = 0.1,
    opacity: float = 0.5,
    seed: int = 1729,
    max_samples: int | None = 150000,
    seen: np.ndarray | None = None,
    source_ids: np.ndarray | None = None,
) -> BuildResult:
    """Sample the mesh, reject degenerate normals, and build bounded Gaussians.

    ``vertex_colours`` must be Nx3 in [0,1].  Samples on degenerate faces are
    excluded up front, then any remaining non-finite normal/colour is rejected
    and reported rather than presented as measured geometry.

    ``seen`` (N bool) marks vertices a photo reached; a sample is supported only
    when all of its triangle's corners are seen.  ``source_ids`` (N str) carries
    the source frame identity for provenance.  Samples without photo support are
    pruned, never exported, and never fall back to neutral grey.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    triangle_arr = np.asarray(triangles, dtype=np.int64)
    vertex_colours = np.asarray(vertex_colours, dtype=np.float64)
    if vertex_colours.shape != (len(vertices), 3):
        raise MetricError(f"vertex_colours must be Nx3 matching vertices, got {vertex_colours.shape}")
    _check_colour_domain(vertex_colours)
    _validate_triangle_indices(triangle_arr, len(vertices))
    if not 0.0 < opacity < 1.0:
        raise MetricError(f"opacity must be in (0,1), got {opacity}")
    if source_ids is not None and (np.asarray(source_ids).shape[0] != len(vertices)):
        raise MetricError("source_ids must have one entry per vertex")
    if seen is not None and np.asarray(seen).shape[0] != len(vertices):
        raise MetricError("seen must have the same count as vertices")

    points, normals, owners, bary_a, bary_b = sample_mesh(
        vertices, triangle_arr, spacing, seed=seed, max_samples=max_samples
    )
    colours = _interpolated_colours(vertex_colours, triangle_arr, owners, bary_a, bary_b)
    per_sample_supported = _sample_support(seen, triangle_arr, owners)
    if not per_sample_supported.any():
        raise MetricError("no supported, photo-observed surface samples remain")
    points, normals, colours, kept_owners, states, rejected = _keep_supported_finite(
        points, normals, colours, owners, per_sample_supported, source_ids, triangle_arr
    )

    frames, scales = surface_covariance(normals, spacing, tangent_factor, normal_ratio)
    sh0 = sh0_from_rgb(np.clip(colours, 0.0, 1.0))
    opacities = np.full(len(points), opacity, dtype=np.float32)
    gaussians = SurfaceGaussians(
        positions=points,
        frames=frames,
        scales=scales,
        opacities=opacities,
        sh0=sh0,
        geometry_only=False,
    )
    return BuildResult(
        gaussians=gaussians,
        supported_count=len(points),
        rejected_count=rejected,
        unsupported_count=int((~per_sample_supported).sum()),
        sample_states=states,
    )


__all__ = ["BuildResult", "SampleState", "build_surface_gaussians", "inverse_sigmoid", "sample_mesh", "sh0_from_rgb"]
