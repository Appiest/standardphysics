"""Attribute viewed-hole faces to a cause with bake-equivalent visibility tests.

For one frozen captured view, the unphotographed faces inside the fixed
measured mask are tested against the bake's own acceptance rule (7-point
support, facing > min_facing, border margin, centre visible) under three
source pools:
  (a) the exact evenly subsampled pool the candidate bake used (camera_count),
  (b) every available capture camera,
  (c) every camera with the facing term relaxed (attribution only).
Depth reference is the candidate display mesh, matching --display-depth
bakes. This is a diagnostic; it never changes assets.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from render_photo_mesh_raster import render_view, scene_arrays  # noqa: E402
from standardphysics_pipeline.ingest import capture_to_room_from_payload  # noqa: E402
from standardphysics_pipeline.render_efficiency.photo_mesh_500 import (  # noqa: E402
    build_captured_view_500,
)
from standardphysics_pipeline.textures.camera import load_cameras  # noqa: E402
from standardphysics_pipeline.textures.surface_photos import surface_depth, visible_points  # noqa: E402

CAPTURES = {
    "center": "454B3661-D3F8-46E8-ADAA-3123E759AA64",
    "top": "BBB88E0A-A2C3-4611-AFE1-71C43454E8C6",
    "bottom_left": "D9946491-26FD-4864-9673-C88180328109",
    "left": "A92A8ED6-87A5-474D-8514-D2D0E6A1F00D",
}
VIS_WIDTH = 1280
MIN_FACING = 0.12


def acceptance(camera, face_corners, display_vertices, display_triangles, depth_stats):
    """Bake-equivalent per-face acceptance and per-cause rejection counts."""
    centres = face_corners.mean(axis=1)
    mids = (face_corners + np.roll(face_corners, 2, axis=1)) / 2
    samples = np.concatenate([face_corners, mids, centres[:, None, :]], axis=1).reshape(-1, 3)
    buffer, raster_cost = depth_stats
    visible, _, _ = visible_points(camera, samples, buffer)
    support = visible.reshape(-1, 7)
    centre_visible = support[:, 6]
    corner_visible = support[:, :3].sum(axis=1) >= 2
    mid_visible = support[:, 3:6].sum(axis=1) >= 2
    normals = np.cross(face_corners[:, 1] - face_corners[:, 0],
                       face_corners[:, 2] - face_corners[:, 0])
    areas = np.linalg.norm(normals, axis=1) / 2
    normals = normals / np.maximum(2 * areas[:, None], 1e-12)
    toward = camera.position - centres
    distance = np.linalg.norm(toward, axis=1)
    facing = (normals * toward).sum(axis=1) / np.maximum(distance, 1e-9)
    corner_u, corner_v, _ = camera.project(face_corners.reshape(-1, 3))
    corner_u, corner_v = corner_u.reshape(-1, 3), corner_v.reshape(-1, 3)
    border_raw = np.minimum.reduce([corner_u, corner_v,
                                    camera.width - 1 - corner_u, camera.height - 1 - corner_v])
    border = np.clip(border_raw.min(axis=1) / 16, 0, 1)
    accepted = (support.sum(axis=1) >= 5) & centre_visible & corner_visible & mid_visible \
        & (facing > MIN_FACING) & (border > 0.02)
    reasons = {
        "depth_support_lt_5": int((support.sum(axis=1) < 5).sum()),
        "centre_not_visible": int((~centre_visible).sum()),
        "corner_mid_spread_fail": int((~(corner_visible & mid_visible)).sum()),
        "facing_below_threshold": int((facing <= MIN_FACING).sum()),
        "border_fail": int((border <= 0.02).sum()),
        "accepted": int(accepted.sum()),
    }
    return accepted, facing, reasons


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--glb", type=Path, required=True)
    parser.add_argument("--view", required=True)
    parser.add_argument("--captures", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--camera-count", type=int, default=160)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    split_doc = json.loads((args.run / "stageA" / "splits-v2.json").read_text())
    if args.view not in split_doc["validation_views_captured"] + split_doc["test_views_captured"]:
        raise SystemExit(f"{args.view} is not a frozen captured view")
    capture_dir = args.captures / CAPTURES[split_doc["capture"]]
    c2r = capture_to_room_from_payload(json.loads((capture_dir / "room.json").read_text()))
    poses = json.loads((capture_dir / "poses.json").read_text())
    frames = {p["frame_id"]: capture_dir / "frames" / Path(p["image"]).name for p in poses}
    cameras = load_cameras(capture_dir / "poses.json", frames, c2r)
    split_ids = set(split_doc["validation_views_captured"]) | set(split_doc["test_views_captured"])
    allowed = [c for c in cameras if c.frame_id not in split_ids]
    sampled_idx = np.linspace(0, len(allowed) - 1, min(args.camera_count, len(allowed)), dtype=int)
    sampled = [allowed[i] for i in sampled_idx]

    arrays = scene_arrays(args.glb)
    by_frame = {c.frame_id: c for c in cameras}
    view_camera = build_captured_view_500(args.view, by_frame[args.view])
    from PIL import Image as PILImage

    coverage_pass = args.out.parent / "full-eval-v5" / f"{args.view}-coverage.png"
    if not coverage_pass.is_file():
        raise SystemExit(f"missing candidate coverage pass: {coverage_pass} (run the evaluator first)")
    fixed = np.asarray(PILImage.open(args.out.parent / "full-eval-v5" / f"{args.view}-fixed-measured.png").convert("L")) > 128
    photographed = np.asarray(PILImage.open(coverage_pass).convert("L")) > 128
    hole_mask = fixed & ~photographed
    outputs = render_view(arrays, view_camera, 500, 500, [3], [args.out / "coverage-check.png"])
    face_buffer = outputs[3]["face_buffer"]
    hole_faces = np.unique(face_buffer[hole_mask])
    hole_faces = hole_faces[(hole_faces >= 0) & (hole_faces < len(arrays[1]))]
    face_corners = arrays[0][arrays[1][hole_faces]]
    print(f"{args.view}: {len(hole_faces)} hole faces, {int(hole_mask.sum())} hole pixels", flush=True)

    results = {
        "view": args.view,
        "glb": str(args.glb),
        "hole_faces": len(hole_faces),
        "hole_pixels": int(hole_mask.sum()),
        "pools": [],
        "note": ("depth reference is the candidate display mesh (matches --display-depth); "
                 "facing-relaxed pool is attribution only, never a bake configuration"),
    }
    per_camera = []
    for camera in allowed:
        small = camera.resized(VIS_WIDTH, round(camera.height * VIS_WIDTH / camera.width))
        t0 = time.time()
        buffer = surface_depth(small, arrays[0], arrays[1])
        acc, facing, reasons = acceptance(small, face_corners, arrays[0], arrays[1], (buffer, 0.0))
        per_camera.append((camera.frame_id, time.time() - t0, acc, facing, reasons))
    allowed_by_id = {record[0]: record for record in per_camera}

    def aggregate(pool, label, relax_facing=False):
        accepted_by_face = np.zeros(len(hole_faces), dtype=int)
        reason_totals = {}
        seconds = 0.0
        for camera in pool:
            _, cost, acc, facing, reasons = allowed_by_id[camera.frame_id]
            seconds += cost
            if relax_facing:
                accepted_by_face += (facing > 0.001).astype(int)
                continue
            accepted_by_face += acc.astype(int)
            for key, value in reasons.items():
                reason_totals[key] = reason_totals.get(key, 0) + value
        return {"pool": label, "cameras_checked": len(pool), "seconds": round(seconds, 1),
                "hole_faces": len(hole_faces),
                "faces_accepted_by_any": int((accepted_by_face > 0).sum()),
                "faces_never_accepted": int((accepted_by_face == 0).sum()),
                "fraction_accepted_by_any": round(float((accepted_by_face > 0).mean()), 4),
                "rejection_reason_totals": reason_totals or None}

    results["pools"] = [
        aggregate(sampled, f"bake_style_subsample_{args.camera_count}"),
        aggregate(allowed, "all_cameras"),
        aggregate(allowed, "all_cameras_facing_relaxed", relax_facing=True),
    ]
    (args.out / "hole-visibility.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
