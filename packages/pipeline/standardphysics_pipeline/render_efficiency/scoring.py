"""Score and hard-gate arithmetic for the frozen render-efficiency benchmark.

The score combines a quality term (PSNR, SSIM, critical-region PSNR) with
resource terms (time, peak footprint, compressed bytes, rendered frame time).
A score is only meaningful once every hard gate passes; helpers here never
silently substitute a missing value for zero.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .metrics import MetricError


def clip(x: float) -> float:
    return min(1.0, max(0.0, x))


def q_psnr(candidate_db: float, baseline_db: float) -> float:
    return clip(0.5 + (candidate_db - baseline_db) / 2.0)


def q_ssim(candidate: float, baseline: float) -> float:
    return clip(0.5 + (candidate - baseline) / 0.04)


def q_critical(min_roi_delta_db: float) -> float:
    return clip(0.5 + min_roi_delta_db / 2.0)


def quality_score(
    candidate_psnr_db: float,
    baseline_psnr_db: float,
    candidate_ssim: float,
    baseline_ssim: float,
    min_roi_delta_db: float,
) -> float:
    """The Q term in [0, 100]. Baseline-against-itself yields exactly 50."""
    return 100.0 * (
        0.40 * q_psnr(candidate_psnr_db, baseline_psnr_db)
        + 0.30 * q_ssim(candidate_ssim, baseline_ssim)
        + 0.30 * q_critical(min_roi_delta_db)
    )


def resource_gain(baseline: float, candidate: float) -> float:
    """100 * clip(0.5 + log2(baseline/candidate)/2); positive finite inputs only."""
    if not (np.isfinite(baseline) and np.isfinite(candidate)) or baseline <= 0 or candidate <= 0:
        raise MetricError(f"resource gain needs positive finite inputs, got {baseline} / {candidate}")
    return 100.0 * clip(0.5 + np.log2(baseline / candidate) / 2.0)


def aggregate_score(
    quality: float,
    baseline_time_s: float,
    candidate_time_s: float,
    baseline_footprint_bytes: float,
    candidate_footprint_bytes: float,
    baseline_spz_bytes: float,
    candidate_spz_bytes: float,
    baseline_frame_p95_ms: float,
    candidate_frame_p95_ms: float,
) -> float:
    """The combined S score. Identical outputs and resources score exactly 50."""
    return (
        0.60 * quality
        + 0.20 * resource_gain(baseline_time_s, candidate_time_s)
        + 0.08 * resource_gain(baseline_footprint_bytes, candidate_footprint_bytes)
        + 0.07 * resource_gain(baseline_spz_bytes, candidate_spz_bytes)
        + 0.05 * resource_gain(baseline_frame_p95_ms, candidate_frame_p95_ms)
    )


@dataclass(frozen=True)
class GateVerdicts:
    """Per-gate results. True=passed, False=failed, None=unknown/not measured."""

    psnr_mean: bool
    psnr_full_mean: bool
    ssim_mean: bool
    critical_roi: bool
    worst_view: bool
    uncovered: bool
    frame_p95: bool | None
    timing_measured: bool
    footprint_measured: bool

    def all_pass(self) -> bool:
        return all(v is True for v in (
            self.psnr_mean, self.psnr_full_mean, self.ssim_mean, self.critical_roi,
            self.worst_view, self.uncovered, self.frame_p95,
            self.timing_measured, self.footprint_measured,
        ))

    def any_unknown(self) -> bool:
        return any(v is None for v in (
            self.psnr_mean, self.psnr_full_mean, self.ssim_mean, self.critical_roi,
            self.worst_view, self.uncovered, self.frame_p95,
            self.timing_measured, self.footprint_measured,
        ))


def hard_gate_verdicts(
    psnr_mean_delta_db: float,
    psnr_full_mean_delta_db: float,
    ssim_mean_delta: float,
    min_roi_delta_db: float,
    worst_view_delta_db: float,
    uncovered_increase: float,
    frame_p95_ratio: float | None,
    timing_measured: bool,
    footprint_measured: bool,
) -> GateVerdicts:
    """Apply the frozen hard numeric gates.

    Each delta is candidate minus baseline (positive means the candidate is
    better).  Missing frame timing stays unknown rather than failing silently.
    """
    return GateVerdicts(
        psnr_mean=psnr_mean_delta_db >= -0.20,
        psnr_full_mean=psnr_full_mean_delta_db >= -0.50,
        ssim_mean=ssim_mean_delta >= -0.005,
        critical_roi=min_roi_delta_db >= -0.30,
        worst_view=worst_view_delta_db >= -1.0,
        uncovered=uncovered_increase <= 0.01,
        frame_p95=(frame_p95_ratio <= 1.10) if frame_p95_ratio is not None else None,
        timing_measured=timing_measured,
        footprint_measured=footprint_measured,
    )
