"""Match two Moffett photo streams with raw 2-D SIFT correspondences.

The matcher never reads LiDAR, room meshes, or pose transforms.  It uses the
stored JPEG pixels only.  A 2-D RANSAC homography is reported as a visual
coherence diagnostic; it is not used to remove raw descriptor matches.

The default ranges cover bottom_left frames 300--413 and left frames 0--210.
The output is JSON plus upright colour correspondence panels under the given
output directory.  The input captures are read-only.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import cv2
import numpy as np

CAPTURES = {
    "bottom_left": "D9946491-26FD-4864-9673-C88180328109",
    "left": "A92A8ED6-87A5-474D-8514-D2D0E6A1F00D",
}

# These pairs were selected for the visual proof panels after the descriptor
# sweep.  The first two show a distinctive dark wall fixture in the same
# corridor; the last one is a deliberately rejected repeated-locker case.
PROOF_PAIRS = {
    "accepted_b411_l58": (411, 58),
    "accepted_b401_l60": (401, 60),
    "accepted_b402_l60": (402, 60),
    "rejected_b360_l184": (360, 184),
}


@dataclass
class FrameFeatures:
    capture: str
    number: int
    path: Path
    image: np.ndarray
    keypoints: list[cv2.KeyPoint]
    descriptors: np.ndarray | None


def _frame_path(captures_root: Path, capture: str, number: int) -> Path:
    return captures_root / CAPTURES[capture] / "frames" / f"frame_{number:04d}.jpg"


def _read_features(
    captures_root: Path,
    capture: str,
    numbers: Iterable[int],
    max_side: int,
    sift: cv2.SIFT,
) -> list[FrameFeatures]:
    result: list[FrameFeatures] = []
    for number in numbers:
        path = _frame_path(captures_root, capture, number)
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise FileNotFoundError(path)
        factor = min(1.0, max_side / max(image.shape))
        if factor != 1.0:
            image = cv2.resize(
                image,
                (max(1, round(image.shape[1] * factor)), max(1, round(image.shape[0] * factor))),
                interpolation=cv2.INTER_AREA,
            )
        keypoints, descriptors = sift.detectAndCompute(image, None)
        result.append(FrameFeatures(capture, number, path, image, keypoints, descriptors))
    return result


def _ratio_matches(
    source: FrameFeatures,
    target: FrameFeatures,
    matcher: cv2.DescriptorMatcher,
    ratio: float,
) -> list[cv2.DMatch]:
    if source.descriptors is None or target.descriptors is None:
        return []
    if len(source.descriptors) < 2 or len(target.descriptors) < 2:
        return []
    nearest = matcher.knnMatch(source.descriptors, target.descriptors, k=2)
    good = [pair[0] for pair in nearest if len(pair) == 2 and pair[0].distance < ratio * pair[1].distance]
    # Repeated lockers can generate several query features for one target
    # feature.  Keep the best descriptor match while retaining every distinct
    # target feature for the raw count.
    unique_target: dict[int, cv2.DMatch] = {}
    for match in good:
        old = unique_target.get(match.trainIdx)
        if old is None or match.distance < old.distance:
            unique_target[match.trainIdx] = match
    return sorted(unique_target.values(), key=lambda match: match.distance)


def _points(source: FrameFeatures, target: FrameFeatures, matches: Sequence[cv2.DMatch]) -> tuple[np.ndarray, np.ndarray]:
    a = np.float32([source.keypoints[match.queryIdx].pt for match in matches])
    b = np.float32([target.keypoints[match.trainIdx].pt for match in matches])
    return a, b


def _unique_indices(a: np.ndarray, b: np.ndarray, indices: Sequence[int], radius: float) -> list[int]:
    """Collapse multiple SIFT orientations at one image location."""
    keep: list[int] = []
    for index in indices:
        if all(np.linalg.norm(a[index] - a[other]) > radius and np.linalg.norm(b[index] - b[other]) > radius for other in keep):
            keep.append(index)
    return keep


def _grid_count(points: np.ndarray, width: int, height: int, columns: int = 4, rows: int = 3) -> int:
    if len(points) == 0:
        return 0
    cells = np.floor(points / np.array([width, height], dtype=np.float32) * np.array([columns, rows])).astype(int)
    cells[:, 0] = np.clip(cells[:, 0], 0, columns - 1)
    cells[:, 1] = np.clip(cells[:, 1], 0, rows - 1)
    return len({(int(x), int(y)) for x, y in cells})


def _diagnose(
    source: FrameFeatures,
    target: FrameFeatures,
    matches: Sequence[cv2.DMatch],
    ransac_threshold: float,
    unique_radius: float,
) -> dict:
    width_s, height_s = source.image.shape[1], source.image.shape[0]
    width_t, height_t = target.image.shape[1], target.image.shape[0]
    result = {
        "raw_matches": len(matches),
        "raw_source_grid_cells": 0,
        "raw_target_grid_cells": 0,
        "homography": None,
        "homography_inliers": 0,
        "unique_inliers": 0,
        "inlier_rmse_px": None,
        "inlier_source_grid_cells": 0,
        "inlier_target_grid_cells": 0,
        "inlier_source_span": [0.0, 0.0],
        "inlier_target_span": [0.0, 0.0],
        "inlier_source_hull_area": 0.0,
        "inlier_target_hull_area": 0.0,
        "matches": [],
    }
    if not matches:
        return result
    a, b = _points(source, target, matches)
    result["raw_source_grid_cells"] = _grid_count(a, width_s, height_s)
    result["raw_target_grid_cells"] = _grid_count(b, width_t, height_t)
    inlier_mask = np.zeros(len(matches), dtype=bool)
    homography = None
    if len(matches) >= 4:
        homography, mask = cv2.findHomography(
            np.ascontiguousarray(a),
            np.ascontiguousarray(b),
            cv2.RANSAC,
            ransac_threshold,
            maxIters=3000,
            confidence=0.999,
        )
        if mask is not None:
            inlier_mask = mask.ravel().astype(bool)
    inlier_indices = np.flatnonzero(inlier_mask).tolist()
    unique_indices = _unique_indices(a, b, inlier_indices, unique_radius)
    result["homography_inliers"] = len(inlier_indices)
    result["unique_inliers"] = len(unique_indices)
    if homography is not None:
        result["homography"] = homography.tolist()
        projected = cv2.perspectiveTransform(a[None, :, :], homography)[0]
        errors = np.linalg.norm(projected - b, axis=1)
        result["inlier_rmse_px"] = float(np.sqrt(np.mean(errors[inlier_mask] ** 2))) if inlier_mask.any() else None
    if unique_indices:
        aa, bb = a[unique_indices], b[unique_indices]
        result["inlier_source_grid_cells"] = _grid_count(aa, width_s, height_s)
        result["inlier_target_grid_cells"] = _grid_count(bb, width_t, height_t)
        result["inlier_source_span"] = [float(x) for x in np.ptp(aa, axis=0)]
        result["inlier_target_span"] = [float(x) for x in np.ptp(bb, axis=0)]
        result["inlier_source_hull_area"] = float(cv2.contourArea(cv2.convexHull(aa)) / (width_s * height_s)) if len(aa) >= 3 else 0.0
        result["inlier_target_hull_area"] = float(cv2.contourArea(cv2.convexHull(bb)) / (width_t * height_t)) if len(bb) >= 3 else 0.0
    for index, match in enumerate(matches):
        result["matches"].append(
            {
                "source_xy": [float(x) for x in a[index]],
                "target_xy": [float(x) for x in b[index]],
                "distance": float(match.distance),
                "homography_inlier": bool(inlier_mask[index]),
                "unique_inlier": index in unique_indices,
            }
        )
    return result


def _pair_record(source: FrameFeatures, target: FrameFeatures, metrics: dict) -> dict:
    return {
        "source_capture": source.capture,
        "source_frame": f"frame_{source.number:04d}",
        "target_capture": target.capture,
        "target_frame": f"frame_{target.number:04d}",
        "source_path": str(source.path),
        "target_path": str(target.path),
        "image_size": [int(source.image.shape[1]), int(source.image.shape[0])],
        "metrics": metrics,
    }


def _rotate_ccw_point(point: Sequence[float], width: int) -> tuple[int, int]:
    return int(round(point[1])), int(round(width - 1 - point[0]))


def _draw_proof(
    source: FrameFeatures,
    target: FrameFeatures,
    metrics: dict,
    output: Path,
    inliers_only: bool,
    max_lines: int = 100,
) -> None:
    source_colour = cv2.cvtColor(source.image, cv2.COLOR_GRAY2BGR)
    target_colour = cv2.cvtColor(target.image, cv2.COLOR_GRAY2BGR)
    source_upright = cv2.rotate(source_colour, cv2.ROTATE_90_COUNTERCLOCKWISE)
    target_upright = cv2.rotate(target_colour, cv2.ROTATE_90_COUNTERCLOCKWISE)
    source_height, source_width = source_upright.shape[:2]
    target_height, target_width = target_upright.shape[:2]
    gap, banner = 36, 36
    canvas_width = max(source_width, target_width)
    canvas = np.full((banner + source_height + gap + target_height + banner, canvas_width, 3), 255, dtype=np.uint8)
    canvas[banner : banner + source_height, :source_width] = source_upright
    target_y = banner + source_height + gap
    canvas[target_y : target_y + target_height, :target_width] = target_upright
    label = "raw ratio matches" if not inliers_only else "2-D homography inliers"
    cv2.putText(canvas, f"bottom_left {source.number:04d}  ->  left {target.number:04d}  ({label})", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (20, 20, 20), 1, cv2.LINE_AA)
    cv2.putText(canvas, f"raw={metrics['raw_matches']}  inliers={metrics['homography_inliers']}  unique={metrics['unique_inliers']}", (10, target_y + target_height + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (20, 20, 20), 1, cv2.LINE_AA)
    matches = metrics["matches"]
    if inliers_only:
        matches = [item for item in matches if item["unique_inlier"]]
    else:
        matches = matches[:max_lines]
    source_image_width = source.image.shape[1]
    for item in matches:
        p0 = _rotate_ccw_point(item["source_xy"], source_image_width)
        p1 = _rotate_ccw_point(item["target_xy"], target.image.shape[1])
        p0 = (p0[0], p0[1] + banner)
        p1 = (p1[0], p1[1] + target_y)
        colour = (40, 190, 40) if inliers_only else (40, 210, 40)
        cv2.line(canvas, p0, p1, colour, 1, cv2.LINE_AA)
        cv2.circle(canvas, p0, 4, colour, 1, cv2.LINE_AA)
        cv2.circle(canvas, p1, 4, colour, 1, cv2.LINE_AA)
    cv2.imwrite(str(output), canvas, [cv2.IMWRITE_JPEG_QUALITY, 92])


def _parse_pair(text: str) -> tuple[int, int]:
    try:
        bottom, left = text.split(":", 1)
        return int(bottom), int(left)
    except ValueError as error:
        raise argparse.ArgumentTypeError("proof pair must be BOTTOM:LEFT, for example 411:58") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", type=Path, default=Path("datasets/phone/moffett"))
    parser.add_argument("--output", type=Path, default=Path("runs/moffett/raw-matches"))
    parser.add_argument("--bottom-start", type=int, default=300)
    parser.add_argument("--bottom-stop", type=int, default=414)
    parser.add_argument("--left-start", type=int, default=0)
    parser.add_argument("--left-stop", type=int, default=211)
    parser.add_argument("--bottom-step", type=int, default=1)
    parser.add_argument("--left-step", type=int, default=1)
    parser.add_argument("--max-side", type=int, default=768)
    parser.add_argument("--nfeatures", type=int, default=2500)
    parser.add_argument("--ratio", type=float, default=0.80)
    parser.add_argument("--checks", type=int, default=32)
    parser.add_argument("--proof-pair", action="append", type=_parse_pair, dest="proof_pairs")
    args = parser.parse_args()
    if args.bottom_step < 1 or args.left_step < 1:
        parser.error("frame steps must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    cv2.setNumThreads(2)
    sift = cv2.SIFT_create(nfeatures=args.nfeatures, contrastThreshold=0.015)
    bottom_numbers = list(range(args.bottom_start, args.bottom_stop, args.bottom_step))
    left_numbers = list(range(args.left_start, args.left_stop, args.left_step))
    bottom_frames = _read_features(args.captures, "bottom_left", bottom_numbers, args.max_side, sift)
    left_frames = _read_features(args.captures, "left", left_numbers, args.max_side, sift)
    matcher = cv2.FlannBasedMatcher(dict(algorithm=1, trees=4), dict(checks=args.checks))
    rows: list[dict] = []
    for source in bottom_frames:
        for target in left_frames:
            matches = _ratio_matches(source, target, matcher, args.ratio)
            metrics = _diagnose(source, target, matches, max(3.0, 5.0 * args.max_side / 960.0), max(4.0, 10.0 * args.max_side / 960.0))
            rows.append(_pair_record(source, target, metrics))
        print(f"matched bottom frame {source.number:04d} against {len(left_frames)} left frames", flush=True)
    # The sort keys are separate: raw descriptor support and the 2-D spatial
    # coherence diagnostic are both retained for auditability.
    by_raw = sorted(rows, key=lambda row: (row["metrics"]["raw_matches"], row["metrics"]["raw_source_grid_cells"], row["metrics"]["raw_target_grid_cells"]), reverse=True)
    by_coherence = sorted(rows, key=lambda row: (row["metrics"]["unique_inliers"], row["metrics"]["homography_inliers"], row["metrics"]["raw_matches"]), reverse=True)
    lookup = {(int(row["source_frame"][-4:]), int(row["target_frame"][-4:])): row for row in rows}
    requested_proofs = args.proof_pairs or list(PROOF_PAIRS.values())
    proof_rows: dict[str, dict] = {}
    for bottom_number, left_number in requested_proofs:
        source = next((frame for frame in bottom_frames if frame.number == bottom_number), None)
        target = next((frame for frame in left_frames if frame.number == left_number), None)
        if source is None or target is None:
            # A proof pair outside a sparse sweep is still evaluated directly.
            source = _read_features(args.captures, "bottom_left", [bottom_number], args.max_side, sift)[0]
            target = _read_features(args.captures, "left", [left_number], args.max_side, sift)[0]
            matches = _ratio_matches(source, target, matcher, args.ratio)
            metrics = _diagnose(source, target, matches, max(3.0, 5.0 * args.max_side / 960.0), max(4.0, 10.0 * args.max_side / 960.0))
            row = _pair_record(source, target, metrics)
        else:
            row = lookup[(bottom_number, left_number)]
        key = f"b{bottom_number:03d}_l{left_number:03d}"
        proof_rows[key] = row
        _draw_proof(source, target, row["metrics"], args.output / f"{key}_raw.jpg", False)
        _draw_proof(source, target, row["metrics"], args.output / f"{key}_inliers.jpg", True)
    summary = {
        "method": {
            "detector": "SIFT",
            "max_side_px": args.max_side,
            "nfeatures": args.nfeatures,
            "ratio_test": args.ratio,
            "descriptor_matcher": "FLANN KD-tree knnMatch, target-index deduplication",
            "uses_lidar_or_pose_geometry": False,
            "homography_role": "diagnostic visual-coherence score only; raw descriptor matches are retained",
        },
        "captures": CAPTURES,
        "ranges": {
            "bottom_left": [args.bottom_start, args.bottom_stop - 1, args.bottom_step],
            "left": [args.left_start, args.left_stop - 1, args.left_step],
        },
        "pairs_evaluated": len(rows),
        "top_by_raw_matches": by_raw[:50],
        "top_by_2d_coherence": by_coherence[:50],
        "proof_pairs": proof_rows,
        "interpretation": {
            "accepted_b411_l58": "Same dark wall fixture, locker bank, sign and pipe corridor are visible in both frames; 2-D inlier lines cluster on the fixture and adjacent corridor details.",
            "accepted_b401_l60": "Near-duplicate viewpoint of the same fixture/locker/sign corridor segment; retained as corroborating frame pair.",
            "accepted_b402_l60": "Same dark wall fixture and locker wall, with fewer but still repeatable raw 2-D correspondences.",
            "rejected_b360_l184": "Repeated locker handles yield many raw descriptor matches, but lines jump between locker positions and the 2-D inlier count is low; reject as physical registration evidence.",
        },
    }
    (args.output / "raw_sift_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"wrote {args.output / 'raw_sift_summary.json'}", flush=True)


if __name__ == "__main__":
    main()
