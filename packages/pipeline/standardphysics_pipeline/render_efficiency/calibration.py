"""Synthetic calibration for deterministic surface-Gaussian rendering.

Two calibrated quantities matter, and they are different physical things:

- **Transmittance composition** (the criterion): front-to-back accumulated
  alpha `1 - prod(1 - alpha_i)` over the samples in ray order.  Two coincident
  alpha-0.5 layers yield 0.75, a wall that fully occludes yields 1.0, and
  brightness never exceeds the surface colour.  The sum of individual alphas is
  kept only as a point-density diagnostic, never as opacity or coverage.
- **The production sampler**: the capped, area-weighted random mesh sampler
  builder.sample_mesh actually exports.  An infinite square lattice does not
  validate it, so the synthetic model below generates randomly oriented,
  nonuniformly dense surfaces at varying distances and evaluates them with
  that sampler.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .builder import sample_mesh
from .metrics import MetricError

TANGENT_FACTORS: tuple[float, ...] = (0.5, 0.75, 1.0)
"""Policy options for tangent sigma = factor * spacing (docs/archive/research/deepseek-lidar-experiment-policy-v2.json)."""

OPACITY_OPTIONS: tuple[float, ...] = (0.3, 0.5, 0.7)
"""Policy options for per-splat opacity."""

SPACING_METRES: tuple[float, ...] = (0.02, 0.05)
"""Sample spacings the frozen choice must be hole-free at (self-similarity is verified, not assumed)."""

NORMAL_TO_TANGENT_RATIO = 0.1
"""Initial normal sigma as a fraction of the tangent sigma (policy value)."""

HOLE_ALPHA_THRESHOLD = 0.5
"""Composited alpha below this reads as a visible hole."""

COVERAGE_FRACTION_LIMIT = 0.05
"""More than this fraction of surface probes below the hole threshold is undersampled."""

TRUNCATION_TOLERANCE = 1e-4
"""Maximum allowed summed-alpha error from ignoring far lattice rings."""

DEFAULT_RINGS = 14
DEFAULT_GRID_SIDE = 101


def _positive_finite(value: float, name: str) -> float:
    if not np.isfinite(value) or value <= 0.0:
        raise MetricError(f"{name} must be positive finite, got {value}")
    return float(value)


def _validated_opacity(opacity: float) -> float:
    if not np.isfinite(opacity) or not 0.0 <= opacity <= 1.0:
        raise MetricError(f"opacity must be in [0,1], got {opacity}")
    return float(opacity)


def _validate_sigma_and_opacity(sigma_t: float, opacity: float) -> None:
    _positive_finite(sigma_t, "sigma_t")
    _validated_opacity(opacity)


def transmittance_alpha(alphas: np.ndarray) -> np.ndarray:
    """Front-to-back accumulated alpha along the last axis.

    ``1 - prod(1 - alpha_i)``; two 0.5 layers -> 0.75, any input in [0,1]
    stays in [0,1].  Rows must already be sorted along the ray, nearest first
    (the caller documents the ray projection and ordering).
    """
    alphas = np.asarray(alphas, dtype=np.float64)
    if alphas.size == 0:
        raise MetricError("transmittance_alpha needs at least one sample")
    if not np.isfinite(alphas).all() or (alphas < 0.0).any() or (alphas > 1.0).any():
        raise MetricError("alphas must be finite and lie in [0,1]")
    survival = np.cumprod(1.0 - alphas, axis=-1)
    return 1.0 - survival


def plane_alpha_sum(distances_sq: np.ndarray, sigma_t: float, opacity: float) -> np.ndarray:
    """Summed alpha over samples for query points — a density diagnostic only.

    ``distances_sq`` is (Nq, Ns): squared in-plane distance from each of Nq
    query points to each of Ns samples.  Returns shape (Nq,).  This sum is
    NOT opacity, brightness or coverage; use transmittance composition for
    those.
    """
    _validate_sigma_and_opacity(sigma_t, opacity)
    distances = np.asarray(distances_sq, dtype=np.float64)
    if distances.ndim != 2:
        raise MetricError(f"distances_sq must be 2D (Nq, Ns), got shape {distances.shape}")
    if not np.isfinite(distances).all():
        raise MetricError("distances_sq must be finite")
    if distances.size and (distances < 0.0).any():
        raise MetricError("distances_sq must be non-negative")
    per_sample = opacity * np.exp(-distances / (2.0 * sigma_t * sigma_t))
    return per_sample.sum(axis=1)


def plane_alpha_composited(distances_sq: np.ndarray, sigma_t: float, opacity: float) -> np.ndarray:
    """Accumulated front-to-back transmittance alpha per query point, shape (Nq,).

    Samples are ordered front-to-back by query distance before composition;
    the ordering is documented here rather than left to array order.  Returns
    the final accumulated alpha so two 0.5 layers read 0.75, and summed alpha
    remains available via :func:`plane_alpha_sum` as a density diagnostic.
    """
    _validate_sigma_and_opacity(sigma_t, opacity)
    distances = np.asarray(distances_sq, dtype=np.float64)
    if distances.ndim != 2:
        raise MetricError(f"distances_sq must be 2D (Nq, Ns), got shape {distances.shape}")
    if not np.isfinite(distances).all():
        raise MetricError("distances_sq must be finite")
    if distances.size and (distances < 0.0).any():
        raise MetricError("distances_sq must be non-negative")
    rows = np.arange(distances.shape[0])[:, None]
    order = np.argsort(distances, axis=1)
    ordered = opacity * np.exp(-distances[rows, order] / (2.0 * sigma_t * sigma_t))
    return transmittance_alpha(ordered)[:, -1]


def lattice_offsets(spacing_h: float, rings: int) -> np.ndarray:
    """Sample positions (M,2) relative to the cell origin on a square lattice.

    Includes every sample with ``|i|, |j| <= rings`` in units of ``spacing_h``.
    """
    _positive_finite(spacing_h, "spacing_h")
    if not isinstance(rings, (int, np.integer)) or rings < 1:
        raise MetricError(f"rings must be a positive integer, got {rings}")
    axis = np.arange(-rings, rings + 1, dtype=np.float64) * spacing_h
    xs, ys = np.meshgrid(axis, axis)
    return np.stack([xs.ravel(), ys.ravel()], axis=1)


def fundamental_cell_grid(spacing_h: float, grid_side: int) -> np.ndarray:
    """Query points (N,2) on a dense square grid covering one cell [0,h]^2."""
    _positive_finite(spacing_h, "spacing_h")
    if not isinstance(grid_side, (int, np.integer)) or grid_side < 2:
        raise MetricError(f"grid_side must be at least 2, got {grid_side}")
    axis = np.linspace(0.0, spacing_h, grid_side)
    xs, ys = np.meshgrid(axis, axis)
    return np.stack([xs.ravel(), ys.ravel()], axis=1)


def _composited_alpha(queries: np.ndarray, offsets: np.ndarray, sigma_t: float, opacity: float) -> np.ndarray:
    """Composited transmittance alpha per query point, processed in row blocks."""
    block_rows = 256
    parts = []
    for start in range(0, len(queries), block_rows):
        block = queries[start : start + block_rows]
        delta = block[:, None, :] - offsets[None, :, :]
        parts.append(plane_alpha_composited((delta * delta).sum(axis=2), sigma_t, opacity))
    return np.concatenate(parts)


def plane_alpha_field(
    spacing_h: float,
    tangent_factor: float,
    opacity: float,
    rings: int = DEFAULT_RINGS,
    grid_side: int = DEFAULT_GRID_SIDE,
) -> np.ndarray:
    """Transmittance-composited alpha field over one fundamental cell."""
    sigma_t = tangent_factor * spacing_h
    _validate_sigma_and_opacity(sigma_t, opacity)
    offsets = lattice_offsets(spacing_h, rings)
    queries = fundamental_cell_grid(spacing_h, grid_side)
    composited = _composited_alpha(queries, offsets, sigma_t, opacity)
    return composited.reshape(grid_side, grid_side)


def truncation_error_bound(tangent_factor: float, opacity: float, rings: int = DEFAULT_RINGS) -> float:
    """Rigorous upper bound on summed alpha from samples beyond ``rings``.

    A query point in [0,h]^2 is at least ``(max(|i|-1, 0)*h, max(|j|-1, 0)*h)``
    away from sample ``(i*h, j*h)`` along each axis, so the omitted tail is
    bounded by ``opacity * (S_inf^2 - S_box^2)`` with
    ``q(n) = exp(-max(|n|-1, 0)^2 / (2 c^2))``, ``S_box`` summed over the
    included box and ``S_inf`` over the full lattice axis (evaluated exactly
    to machine precision).
    """
    c = _positive_finite(tangent_factor, "tangent_factor")
    o = _validated_opacity(opacity)
    if not isinstance(rings, (int, np.integer)) or rings < 1:
        raise MetricError(f"rings must be a positive integer, got {rings}")
    axis_max = math.ceil(math.sqrt(2.0 * c * c * 700.0)) + 2
    axis_all = np.arange(axis_max + 1, dtype=np.float64)
    q_all = np.exp(-(np.maximum(axis_all - 1.0, 0.0) ** 2) / (2.0 * c * c))
    s_infinite = 1.0 + 2.0 * q_all[1:].sum()
    box = np.arange(-rings, rings + 1, dtype=np.float64)
    q_box = np.exp(-(np.maximum(np.abs(box) - 1.0, 0.0) ** 2) / (2.0 * c * c))
    s_box = q_box.sum()
    return o * (s_infinite * s_infinite - s_box * s_box)


def fill_status(min_composited_alpha: float, hole_fraction: float) -> str:
    """Classify one numeric result on composited transmittance.

    Brightness saturation is not a thing here: an occluded wall legitimately
    composited to alpha 1.0, and brightness is bounded by the surface colour
    (verified on real renders, see the calibration artifact).  Only holes
    classify a configuration.
    """
    if min_composited_alpha < HOLE_ALPHA_THRESHOLD or hole_fraction > COVERAGE_FRACTION_LIMIT:
        return "holes"
    return "no_holes"


def evaluate_pair(
    spacing_h: float,
    tangent_factor: float,
    opacity: float,
    rings: int = DEFAULT_RINGS,
    grid_side: int = DEFAULT_GRID_SIDE,
) -> dict:
    """Per-spacing synthetic metrics for one (tangent_factor, opacity) pair."""
    field = plane_alpha_field(spacing_h, tangent_factor, opacity, rings=rings, grid_side=grid_side)
    hole_fraction = float((field < HOLE_ALPHA_THRESHOLD).mean())
    return {
        "spacing_h": float(spacing_h),
        "tangent_factor": float(tangent_factor),
        "opacity": float(opacity),
        "min_composited_alpha": float(field.min()),
        "hole_fraction": hole_fraction,
        "truncation_bound": truncation_error_bound(tangent_factor, opacity, rings=rings),
        "rings": int(rings),
        "grid_side": int(grid_side),
    }


def evaluate_all(
    spacings: tuple[float, ...] = SPACING_METRES,
    factors: tuple[float, ...] = TANGENT_FACTORS,
    opacities: tuple[float, ...] = OPACITY_OPTIONS,
    rings: int = DEFAULT_RINGS,
    grid_side: int = DEFAULT_GRID_SIDE,
) -> dict:
    """Full synthetic sweep keyed by (tangent_factor, opacity) -> per-spacing entries."""
    sweep: dict = {}
    for factor in factors:
        for opacity in opacities:
            sweep[(factor, opacity)] = [
                evaluate_pair(h, factor, opacity, rings=rings, grid_side=grid_side) for h in spacings
            ]
    return sweep


def _pair_status(entries: list[dict]) -> str:
    """Worst status of one pair across all evaluated spacings."""
    per_spacing = [fill_status(e["min_composited_alpha"], e["hole_fraction"]) for e in entries]
    return "holes" if "holes" in per_spacing else "no_holes"


def _primary_choice(sweep: dict) -> tuple[float, float]:
    """Smallest factor per opacity with no holes at both spacings."""
    for opacity in OPACITY_OPTIONS:
        for factor in TANGENT_FACTORS:
            if _pair_status(sweep[(factor, opacity)]) != "holes":
                return factor, opacity
    raise MetricError("no policy option leaves the synthetic plane hole-free at both spacings")


def _rationale(sweep: dict, chosen: tuple[float, float]) -> str:
    factor, opacity = chosen
    entries = sweep[chosen]
    mins = " / ".join(f"{e['min_composited_alpha']:.4f}" for e in entries)
    statuses = "; ".join(
        f"c={f},o={o}:{_pair_status(sweep[(f, o)])}" for f in TANGENT_FACTORS for o in OPACITY_OPTIONS
    )
    return (
        "Square-lattice plane at h in {0.02, 0.05} m with front-to-back "
        "transmittance compositing (1 - prod(1-alpha_i)); a lattice is a "
        "diagnostic only, the production capped sampler is calibrated "
        "separately in the calibration artifact. "
        f"Pair statuses: {statuses}. Frozen pair (c={factor}, o={opacity}): "
        f"min composited alpha {mins}; brightness is bounded by surface "
        "colour and never read as alpha."
    )


def pick_parameters() -> dict:
    """Freeze (tangent_factor, opacity) from the composited synthetic-plane sweep.

    The chosen pair must be hole-free at both spacings; brightness saturation
    is no longer a criterion (constant-colour behaviour is renderer-verified).
    The returned pair is the policy-option table decision, superseding the
    obsolete tangenta=0.5/opacity=0.5 freeze under the new calibration
    artifact ID.
    """
    sweep = evaluate_all()
    chosen = _primary_choice(sweep)
    factor, opacity = chosen
    entries = sweep[chosen]
    return {
        "tangent_factor": factor,
        "opacity": opacity,
        "rationale": _rationale(sweep, chosen),
        "min_composited_alpha_h002": entries[0]["min_composited_alpha"],
        "min_composited_alpha_h005": entries[1]["min_composited_alpha"],
        "hole_fraction": max(e["hole_fraction"] for e in entries),
        "note": "supersedes the unsupported 0.5/0.5 freeze; compositing corrected",
    }


def synthetic_calibration_mesh(seed: int = 11) -> tuple[np.ndarray, np.ndarray]:
    """Randomly oriented, nonuniformly dense triangle surfaces at varied distances.

    Returns a mesh whose triangles point in many directions (walls, tilted
    planes), whose areas span orders of magnitude, and whose parts sit at
    different distances from one another — the properties the square lattice
    lacks.
    """
    generator = np.random.default_rng(seed)
    vertices, triangles = [], []
    offset = 0
    for plane_idx in range(6):
        axis = generator.standard_normal(3)
        axis /= np.linalg.norm(axis)
        u = np.cross(axis, np.array([1.0, 0.0, 0.0]) + 1e-3)
        u /= np.maximum(np.linalg.norm(u), 1e-12)
        v = np.cross(axis, u)
        centre = generator.uniform(-2.0, 2.0, 3) * (1.0 + plane_idx / 3.0)
        half = generator.uniform(0.4, 1.2)
        base = np.array([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]]) * half
        corners = centre + base @ np.stack([u, v])
        start = offset
        vertices.extend(corners.tolist())
        triangles.extend([[start, start + 1, start + 2], [start, start + 2, start + 3]])
        offset += 4
        # one dense neighbour plane + one sparse distant plane per group
        for burst in (0.5, 2.5):
            small = base * 0.18 * burst
            corners_small = centre + small @ np.stack([u, v])
            start = offset
            vertices.extend(corners_small.tolist())
            triangles.extend([[start, start + 1, start + 2], [start, start + 2, start + 3]])
            offset += 4
    return np.asarray(vertices), np.asarray(triangles, dtype=np.int64)


def calibrate_capped_sampler(
    spacing: float = 0.02,
    probe_count: int = 600,
    seed: int = 1729,
    max_samples: int = 150000,
) -> dict:
    """Evaluate tangent/opacity policy options with the production sampler.

    Samples the synthetic mesh exactly like the builder does (random
    barycentric placement, bounded allocation), probes the surface with
    independent area-weighted barycentric points, and measures
    transmittance-composited coverage for each policy option (chunked over
    samples so memory stays bounded).  The chosen pair is the option with the
    smallest hole fraction, tie-broken to the smaller tangent factor, and the
    result is an honest diagnostic, never a pass claim on its own.
    """
    vertices, triangles = synthetic_calibration_mesh()
    _positive_finite(spacing, "spacing")
    report: dict[str, Any] = {"model": "capped random mesh sampler", "spacing_m": spacing, "options": []}
    samples = sample_mesh(vertices, triangles, spacing=spacing, seed=seed, max_samples=max_samples)
    points, _, owners, _, _ = samples
    report["sample_count"] = int(len(points))
    report["cap"] = max_samples
    report["density_area_agreement"] = _density_agreement(vertices, triangles, owners)

    corners_all = vertices[triangles]
    areas = np.linalg.norm(
        np.cross(corners_all[:, 1] - corners_all[:, 0], corners_all[:, 2] - corners_all[:, 0]),
        axis=1,
    ) / 2.0
    weights = areas / areas.sum()
    probe_rng = np.random.default_rng(seed + 1)
    probe_triangles = probe_rng.choice(len(triangles), size=probe_count, p=weights)
    probe_corners = vertices[triangles[probe_triangles]]
    alpha_rng = probe_rng.random((probe_count, 2))
    bary = np.stack(
        [1 - np.sqrt(alpha_rng[:, 0]), np.sqrt(alpha_rng[:, 0]) * (1 - alpha_rng[:, 1])],
        axis=1,
    )
    bary_c = 1 - bary.sum(axis=1)
    probes = (
        bary[:, 0][:, None] * probe_corners[:, 0]
        + bary[:, 1][:, None] * probe_corners[:, 1]
        + bary_c[:, None] * probe_corners[:, 2]
    )

    for factor in TANGENT_FACTORS:
        for opacity in OPACITY_OPTIONS:
            sigma_t = factor * spacing
            survival = np.ones(probe_count)
            for block in np.array_split(points, 20):
                delta_sq = ((probes[:, None, :] - block[None, :, :]) ** 2).sum(axis=2)
                per_block = opacity * np.exp(-delta_sq / (2.0 * sigma_t * sigma_t))
                survival *= (1.0 - per_block).prod(axis=1)
            composited = 1.0 - survival
            report["options"].append({
                "tangent_factor": factor,
                "opacity": opacity,
                "hole_fraction": float((composited < HOLE_ALPHA_THRESHOLD).mean()),
                "min_composited_alpha": float(composited.min()),
            })

    chosen = min(report["options"], key=lambda o: (o["hole_fraction"], o["tangent_factor"]))
    report["chosen"] = chosen
    report["order_note"] = (
        "transmittance composited over samples chunked by memory bound; "
        "per-probe product of survivals is order-independent"
    )
    report["renderer_check"] = (
        "constant-colour fidelity is verified on the installed SplatX renderer in the calibration artifact"
    )
    return report


def _density_agreement(vertices: np.ndarray, triangles: np.ndarray, owners: np.ndarray) -> float:
    """1 - max |triangle emitted fraction - area fraction|, over triangles."""
    corners = vertices[triangles]
    areas = np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1) / 2.0
    expected = areas / areas.sum()
    observed = np.bincount(owners, minlength=len(triangles)).astype(np.float64)
    observed /= observed.sum()
    return float(1.0 - np.abs(expected - observed).max())
