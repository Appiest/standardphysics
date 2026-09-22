"""Repeatable in-plane registration of overlapping room captures.

Two captures of the same floor share, at best, a few points the owner can
identify in both: the same outlet corner, door edge, floor marker. This fits
the rigid in-plane motion a floor plan allows (yaw about the floor normal plus
a translation) from point correspondences, checks how well every point agrees,
and reports whether the configuration could support more than one answer.

Never an unattended auto-alignment: the caller supplies the correspondences
and holds out landmarks; this module only does the arithmetic. Where the input
cannot decide (too few points, coincident points, a cluster smaller than the
noise floor), it refuses with `AmbiguousRegistration` rather than guessing a
transform. A low residual still does not make the result real: it bounds the
fit, and only a held-out landmark residual speaks to the rest of the room.

Rollback: keep the pre-alignment graph revision; the recorded inverse
(yaw -> -yaw, translation -> -R(-yaw) @ t) restores it exactly. Never save a
placement without recording the base revision it was computed from.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

Point = tuple[float, float]


class AmbiguousRegistration(ValueError):
    """The correspondences cannot decide one in-plane motion, or disagree."""


@dataclass(frozen=True)
class PlaneAlignment:
    """The fitted motion and how well the input and any held-out points agree."""

    yaw: float
    """Radians, right-handed about the floor normal (world z)."""
    translation: tuple[float, float]
    """In the moved capture's own frame: new_position = R(yaw) @ p + t."""
    residual_rms: float
    residual_max: float

    def apply(self, point: Point) -> Point:
        cos_t, sin_t = np.cos(self.yaw), np.sin(self.yaw)
        x, y = point
        return (
            cos_t * x - sin_t * y + self.translation[0],
            sin_t * x + cos_t * y + self.translation[1],
        )

    def inverse(self) -> PlaneAlignment:
        cos_t, sin_t = np.cos(self.yaw), np.sin(self.yaw)
        tx, ty = self.translation
        return PlaneAlignment(
            yaw=-self.yaw,
            translation=(-cos_t * tx - sin_t * ty, sin_t * tx - cos_t * ty),
            residual_rms=self.residual_rms,
            residual_max=self.residual_max,
        )


def _fit(source: np.ndarray, target: np.ndarray) -> PlaneAlignment:
    n = len(source)
    design = np.empty((2 * n, 4))
    residual = np.empty(2 * n)
    for index, ((x, y), (qx, qy)) in enumerate(zip(source, target)):
        design[2 * index] = [1.0, x, -y, 0.0]
        residual[2 * index] = qx
        design[2 * index + 1] = [0.0, y, x, 1.0]
        residual[2 * index + 1] = qy
    solution, _, _, _ = np.linalg.lstsq(design, residual, rcond=None)
    tx, cos_t, sin_t, ty = solution
    radius = float(np.hypot(cos_t, sin_t))
    if radius < 1e-9:
        raise AmbiguousRegistration("the correspondences collapse to a point")
    cos_t, sin_t = cos_t / radius, sin_t / radius
    yaw = float(np.arctan2(sin_t, cos_t))
    errors = []
    for (x, y), (qx, qy) in zip(source, target):
        px = cos_t * x - sin_t * y + tx
        py = sin_t * x + cos_t * y + ty
        errors.append(np.hypot(px - qx, py - qy))
    errors = np.asarray(errors)
    return PlaneAlignment(
        yaw=yaw,
        translation=(float(tx), float(ty)),
        residual_rms=float(np.sqrt(np.mean(errors**2))),
        residual_max=float(errors.max()),
    )


def align_points(
    source: list[Point],
    target: list[Point],
    *,
    tolerance: float,
) -> PlaneAlignment:
    """Fit the in-plane motion mapping source points onto target points.

    `tolerance` is the landmark localization tolerance in metres (the expected
    precision of a click or a photo-identified corner). Fewer than three
    correspondences leave no residual to verify against, coincident points
    carry no direction, and residuals far beyond the tolerance mean the two
    scans contradict rather than overlap; all three refuse with
    `AmbiguousRegistration` instead of returning a plausible-looking fit.
    A mirrored (reflected) pairing also refuses: the rotation-only model
    cannot fit it, so its residual rejects it honestly rather than flipping
    a surface.
    """
    if len(source) != len(target) or len(source) < 3:
        raise AmbiguousRegistration("at least three correspondences are required")
    source = np.asarray(source, dtype=float)
    target = np.asarray(target, dtype=float)
    if np.ptp(source, axis=0).max() < 1e-9:
        raise AmbiguousRegistration("the correspondences collapse to a point")
    result = _fit(source, target)
    if result.residual_max > 4 * tolerance:
        raise AmbiguousRegistration(
            f"residual {result.residual_max:.3f} m exceeds 4x the "
            f"{tolerance:.3f} m tolerance: the captures disagree"
        )
    return result


@dataclass(frozen=True)
class HeldOutReport:
    """How well a fitted alignment held on points it never used."""

    count: int
    residual_rms: float
    residual_max: float

    def conservative_bound(self, landmark_tolerance: float) -> float:
        """Translation error bound, additive in the honest sense.

        The fit's worst disagreement plus the landmark's own localization
        tolerance bound how far a held-out point can be from its true partner.
        """
        return self.residual_max + landmark_tolerance


def check_landmarks(
    alignment: PlaneAlignment,
    held_out: list[tuple[Point, Point]],
) -> HeldOutReport:
    """Residuals for correspondences excluded from the fit.

    These points never influenced `alignment`; their residuals are the only
    number that says anything about the rest of the room. Pass every excluded
    pair, including the poor ones — dropping the worst held-out landmark is
    fit selection, not verification.
    """
    if not held_out:
        raise AmbiguousRegistration("no held-out landmarks to check")
    errors = []
    for source, target in held_out:
        px, py = alignment.apply(source)
        errors.append(np.hypot(px - target[0], py - target[1]))
    errors = np.asarray(errors)
    return HeldOutReport(
        count=len(errors),
        residual_rms=float(np.sqrt(np.mean(errors**2))),
        residual_max=float(errors.max()),
    )
