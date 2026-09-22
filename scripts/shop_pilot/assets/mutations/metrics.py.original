"""Paired-image metrics for render-efficiency evaluation.

Every metric reads decoded sRGB images as float64 in [0, 1].  The language model
must never type a metric value into a result file; these functions compute from
actual arrays and raise on evidence that cannot be scored.

Bad evidence fails loudly instead of turning into zero loss or dropping out of
an average: non-finite pixels, missing images, and shape mismatches all raise
:class:`MetricError`.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

PSNR_CAP_DB = 100.0
"""Identical images would otherwise be infinite; the cap is applied to all runs."""

PSNR_MSE_FLOOR = 1e-10
COVERAGE_ALPHA_THRESHOLD = 0.5

_SSIM_WINDOW = 11
_SSIM_SIGMA = 1.5
_SSIM_DATA_RANGE = 1.0
_SSIM_K1 = 0.01
_SSIM_K2 = 0.03


class MetricError(ValueError):
    """Raised when an artifact cannot be scored, never when it simply scores low."""


def _as_float64(image: np.ndarray, name: str) -> np.ndarray:
    array = np.asarray(image)
    if array.ndim != 3 or array.shape[2] != 3:
        raise MetricError(f"{name} must be a HxWx3 RGB image, got shape {array.shape}")
    if array.size == 0:
        raise MetricError(f"{name} is empty")
    converted = array.astype(np.float64)
    if not np.isfinite(converted).all():
        raise MetricError(f"{name} contains NaN or Inf")
    return converted


def _as_mask(mask: np.ndarray | None, reference: np.ndarray, name: str = "mask") -> np.ndarray:
    if mask is None:
        return np.ones(reference.shape[:2], dtype=bool)
    array = np.asarray(mask)
    if array.shape != reference.shape[:2]:
        raise MetricError(f"{name} shape {array.shape} does not match image {reference.shape[:2]}")
    if array.ndim != 2:
        raise MetricError(f"{name} must be 2D, got {array.ndim}D")
    converted = array.astype(bool)
    if not converted.any():
        raise MetricError(f"{name} selects no pixels")
    return converted


def masked_mse(reference: np.ndarray, candidate: np.ndarray, mask: np.ndarray | None = None) -> float:
    """Channel-averaged mean squared error over the selected pixels, float64."""
    reference = _as_float64(reference, "reference")
    candidate = _as_float64(candidate, "candidate")
    if reference.shape != candidate.shape:
        raise MetricError(f"shape mismatch: {reference.shape} vs {candidate.shape}")
    keep = _as_mask(mask, reference)
    squared = (reference - candidate) ** 2
    return float(squared[keep].mean())


def psnr(reference: np.ndarray, candidate: np.ndarray, mask: np.ndarray | None = None) -> float:
    """PSNR in dB, channel-averaged masked MSE, capped at :data:`PSNR_CAP_DB`."""
    mse = masked_mse(reference, candidate, mask)
    value = 10.0 * np.log10(1.0 / max(mse, PSNR_MSE_FLOOR))
    return float(min(value, PSNR_CAP_DB))


def _ssim_kernel() -> np.ndarray:
    axis = np.arange(_SSIM_WINDOW) - (_SSIM_WINDOW - 1) / 2.0
    one_d = np.exp(-(axis**2) / (2.0 * _SSIM_SIGMA**2))
    one_d /= one_d.sum()
    kernel = np.outer(one_d, one_d)
    return kernel / kernel.sum()


def _ssim_map(reference: np.ndarray, candidate: np.ndarray) -> np.ndarray:
    kernel = _ssim_kernel()
    c1 = (_SSIM_K1 * _SSIM_DATA_RANGE) ** 2
    c2 = (_SSIM_K2 * _SSIM_DATA_RANGE) ** 2
    axes = (0, 1)
    mean_x = ndimage.correlate(reference, kernel, mode="reflect", axes=axes)
    mean_y = ndimage.correlate(candidate, kernel, mode="reflect", axes=axes)
    var_x = ndimage.correlate(reference * reference, kernel, mode="reflect", axes=axes) - mean_x**2
    var_y = ndimage.correlate(candidate * candidate, kernel, mode="reflect", axes=axes) - mean_y**2
    cov_xy = ndimage.correlate(reference * candidate, kernel, mode="reflect", axes=axes) - mean_x * mean_y
    luminance = (2 * mean_x * mean_y + c1) / (mean_x**2 + mean_y**2 + c1)
    structure = (2 * cov_xy + c2) / (var_x + var_y + c2)
    return luminance * structure


def ssim(reference: np.ndarray, candidate: np.ndarray, mask: np.ndarray | None = None) -> float:
    """Channel-averaged SSIM, averaged only over windows wholly inside the mask.

    Gaussian 11x11 kernel, sigma 1.5, data range 1, population covariance.  A
    window centre counts only when its entire 11x11 support sits inside the
    fixed valid mask, so masked-off pixels cannot create artificial agreement
    at the border.
    """
    reference = _as_float64(reference, "reference")
    candidate = _as_float64(candidate, "candidate")
    if reference.shape != candidate.shape:
        raise MetricError(f"shape mismatch: {reference.shape} vs {candidate.shape}")
    keep = _as_mask(mask, reference)
    per_channel = _ssim_map(reference, candidate)
    channel_mean = per_channel.mean(axis=2)
    structure = np.ones((_SSIM_WINDOW, _SSIM_WINDOW), dtype=bool)
    valid_centres = ndimage.binary_erosion(keep, structure=structure)
    if not valid_centres.any():
        raise MetricError("mask has no window entirely inside it (too small or too thin)")
    return float(channel_mean[valid_centres].mean())


def uncovered_fraction(
    alpha: np.ndarray, supported_mask: np.ndarray, threshold: float = COVERAGE_ALPHA_THRESHOLD
) -> float:
    """Fraction of known-photo-supported pixels whose appearance alpha is below threshold."""
    alpha = np.asarray(alpha).astype(np.float64)
    if not np.isfinite(alpha).all():
        raise MetricError("alpha contains NaN or Inf")
    supported = _as_mask(supported_mask, alpha, name="supported_mask")
    if alpha.shape != supported.shape:
        raise MetricError("alpha and supported_mask must share one spatial shape")
    covered = (alpha >= threshold)
    return float(1.0 - covered[supported].mean())
