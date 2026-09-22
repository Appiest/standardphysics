"""Benchmark moffett-photo-mesh-500: freeze-conformant artifact evaluation.

Gates are computed from actual rendered artifacts against FIXED measured masks:
the per-view denominator is the full-resolution measured geometry rasterized
once per frozen view, independent of the candidate GLB's own mesh or alpha.
Coverage, holes, ROI, weaker-axis source/UV resolution (with active mip) and
edge profiles are all computed from those artifacts; caller-supplied pass
booleans are never evidence and the evaluator exits nonzero when required
evidence is missing or wrong-sized.

Split rule: no frame in the frozen split (validation or test) may appear in
the candidate's bake source list; the evaluator verifies that directly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from render_photo_mesh_raster import render_view, scene_arrays  # noqa: E402
from standardphysics_pipeline.ingest import capture_to_room_from_payload  # noqa: E402
from standardphysics_pipeline.render_efficiency.photo_mesh_500 import (  # noqa: E402
    FINAL,
    build_captured_view_500,
    build_novel_view,
    edge_width_profiles,
    load_reference_500,
    psnr_ssim,
    resolution_gate_stats,
    weaker_axis_resolution,
)
from standardphysics_pipeline.textures.camera import load_cameras  # noqa: E402
from standardphysics_pipeline.textures.scan_colour import scan_geometry  # noqa: E402

CAPTURES = {
    "center": "454B3661-D3F8-46E8-ADAA-3123E759AA64",
    "top": "BBB88E0A-A2C3-4611-AFE1-71C43454E8C6",
    "bottom_left": "D9946491-26FD-4864-9673-C88180328109",
    "left": "A92A8ED6-87A5-474D-8514-D2D0E6A1F00D",
}

SCENE = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])


class FrozenView:
    def __init__(self, id, camera, width, height, reference):
        self.id = id
        self.camera = camera
        self.width = width
        self.height = height
        self.reference = reference


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_splits(run_dir: Path) -> dict:
    for name in ("splits-v2.json", "splits.json"):
        candidate = run_dir / "stageA" / name
        if candidate.is_file():
            return json.loads(candidate.read_text())
    raise SystemExit(f"no splits-v2.json/splits.json under {run_dir}/stageA")


def build_transfer_views(run_dir: Path, room: str) -> list[FrozenView]:
    manifest = json.loads((run_dir / "stageA" / "transfer-novel-views.json").read_text())
    views = []
    for spec in manifest[room]["views"]:
        camera = build_novel_view(spec)
        views.append(FrozenView(spec["id"], camera, camera.width, camera.height, None))
    return views


def build_views(run_dir: Path, captures_dir: Path) -> list[FrozenView]:
    split_doc = load_splits(run_dir)
    capture = CAPTURES[split_doc["capture"]]
    directory = captures_dir / capture
    c2r = capture_to_room_from_payload(json.loads((directory / "room.json").read_text()))
    poses = json.loads((directory / "poses.json").read_text())
    frames = {p["frame_id"]: directory / "frames" / Path(p["image"]).name for p in poses}
    cameras = load_cameras(directory / "poses.json", frames, c2r)
    by_frame = {c.frame_id: c for c in cameras}
    views = []
    for frame_id in split_doc["validation_views_captured"] + split_doc["test_views_captured"]:
        camera = build_captured_view_500(frame_id, by_frame[frame_id])
        views.append(FrozenView(frame_id, camera, camera.width, camera.height,
                                load_reference_500(frames[frame_id])))
    for novel in split_doc["novel_views"]:
        camera = build_novel_view(novel)
        views.append(FrozenView(novel["id"], camera, camera.width, camera.height, None))
    return views


def measured_arrays(directory: Path, c2r) -> tuple:
    """Full-resolution measured geometry in GLTF scene coordinates."""
    vertices, triangles = scan_geometry(directory / "lidar-mesh.json", c2r)
    scene_v = np.column_stack([vertices[:, 0], vertices[:, 2], -vertices[:, 1]]).astype(np.float32)
    faces = np.asarray(triangles, dtype=np.int32)
    uvs = np.zeros((len(scene_v), 2), dtype=np.float32)
    texture_index = np.full(len(faces), -1, dtype=np.int32)
    palette = np.zeros((1, 3), dtype=np.float32)
    return scene_v, faces, uvs, texture_index, [], palette, ["measured"], [], \
        np.full(len(faces), -1, dtype=np.int32)


def render_measured_masks(measured, view, out_dir: Path, region_box: dict | None) -> dict:
    """Fixed per-view masks from the full measured geometry: the same
    denominator for every candidate."""
    math = {}
    geometry_path = out_dir / f"{view.id}-fixed-measured.png"
    render_view(measured, view.camera, FINAL, FINAL, [1], [geometry_path])
    mask = np.asarray(Image.open(geometry_path).convert("L")) > 128
    math["fixed_measured_pixels"] = int(mask.sum())
    math["fixed_measured_fraction_of_frame"] = float(mask.mean())
    roi_path = out_dir / f"{view.id}-fixed-roi.png"
    if region_box is not None:
        room_lo, room_hi = np.asarray(region_box[0]), np.asarray(region_box[1])
        scene_points = measured[0]  # measured scene vertices
        lo = np.minimum.reduce([SCENE @ room_lo, SCENE @ room_hi])
        hi = np.maximum.reduce([SCENE @ room_lo, SCENE @ room_hi])
        inside = ((scene_points[:, 0] >= lo[0]) & (scene_points[:, 0] <= hi[0]) &
                  (scene_points[:, 1] >= lo[1]) & (scene_points[:, 1] <= hi[1]) &
                  (scene_points[:, 2] >= lo[2]) & (scene_points[:, 2] <= hi[2]))
        faces_inside = inside[measured[1]].all(axis=1)
        roi_arrays = list(measured)
        roi_texture = np.where(faces_inside, 0, -1).astype(np.int32)
        roi_arrays[3] = roi_texture
        render_view(tuple(roi_arrays), view.camera, FINAL, FINAL, [3], [roi_path])
        roi_mask = np.asarray(Image.open(roi_path).convert("L")) > 128
        math["roi_fixed_pixels"] = int(roi_mask.sum())
    else:
        roi_mask = np.zeros((FINAL, FINAL), dtype=bool)
        Image.fromarray(np.zeros((FINAL, FINAL), dtype=np.uint8), "L").save(roi_path)
        math["roi_fixed_pixels"] = 0
    return mask, roi_mask, math


def feature_matches(reference: np.ndarray, candidate: np.ndarray, max_features=500, ratio=0.75):
    try:
        import cv2
    except ImportError:
        return {"available": False}
    sift = cv2.SIFT_create(nfeatures=max_features)
    ref_grey = cv2.cvtColor(reference.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    cand_grey = cv2.cvtColor(candidate.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    kp1, des1 = sift.detectAndCompute(ref_grey, None)
    kp2, des2 = sift.detectAndCompute(cand_grey, None)
    if des1 is None or des2 is None or not len(des1) or not len(des2):
        return {"available": True, "keypoints": (len(kp1), len(kp2)), "matches": 0}
    raw = cv2.BFMatcher().knnMatch(des1, des2, k=2)
    good = [pair[0] for pair in raw if len(pair) == 2 and pair[0].distance < ratio * pair[1].distance]
    errors = [np.linalg.norm(np.array(kp1[m.queryIdx].pt) - np.array(kp2[m.trainIdx].pt)) for m in good]
    return {"available": True, "keypoints": (len(kp1), len(kp2)), "matches": len(good),
            "median_error_px": float(np.median(errors)) if errors else None,
            "p95_error_px": float(np.percentile(errors, 95)) if errors else None,
            "machine_note": "SIFT descriptor matches, provisional alignment evidence"}


def hole_metrics(photographed_path: Path, fixed_mask: np.ndarray):
    """Unphotographed connected components inside the FIXED measured target."""
    from scipy import ndimage

    photographed = np.asarray(Image.open(photographed_path).convert("L")) > 128
    untextured = fixed_mask & ~photographed
    if untextured.sum() == 0:
        return {"largest_hole_fraction": 0.0, "hole_count": 0,
                "target_pixels": int(fixed_mask.sum()), "hole_fraction_total": 0.0}, untextured
    labels, count = ndimage.label(untextured)
    sizes = ndimage.sum(untextured, labels, range(1, count + 1))
    return {"largest_hole_fraction": float(sizes.max() / max(fixed_mask.sum(), 1)),
            "hole_count": int(count), "target_pixels": int(fixed_mask.sum()),
            "hole_fraction_total": float(untextured.sum() / max(fixed_mask.sum(), 1))}, untextured


def hole_surface_decomposition(face_buffer: np.ndarray, hole_mask: np.ndarray,
                               scene_vertices: np.ndarray, scene_faces: np.ndarray) -> dict:
    """Orientation breakdown of hole pixels using the candidate's visible faces.

    Hole pixels map through the candidate's face buffer into its scene
    geometry; each face is classified by its normal's dominant axis (down / up
    / lateral), so a coverage failure is attributable to a physical surface
    group rather than a mysterious blob.
    """
    hole_faces = face_buffer[hole_mask]
    hole_faces = hole_faces[(hole_faces >= 0) & (hole_faces < len(scene_faces))]
    if not len(hole_faces):
        return {"hole_pixels_with_face": 0, "by_normal_axis": {}}
    faces = scene_faces[hole_faces]
    corners = scene_vertices[faces]
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    norms = np.linalg.norm(cross, axis=1)
    zero = norms <= 1e-12
    normals = np.where(zero[:, None], np.zeros_like(cross), cross / np.maximum(norms[:, None], 1e-12))
    dominant = np.argmax(np.abs(normals), axis=1)
    vertical_axis = 2  # scene (x, z, -y): vertical is axis 2, +z points down
    down_faces = (dominant == vertical_axis) & (normals[:, vertical_axis] > 0)
    up_faces = (dominant == vertical_axis) & (normals[:, vertical_axis] <= 0)
    lateral = dominant != vertical_axis
    buckets = {"hole_pixels_with_face": int(len(hole_faces)),
               "down_facing_undersides_face_pixels": int(down_faces.sum()),
               "up_facing_tops_floor_face_pixels": int(up_faces.sum()),
               "lateral_walls_face_pixels": int(lateral.sum())}
    buckets["note"] = "normal of candidate visible face per hole pixel; attribution, not area; " \
                      "scene vertical axis 2 (+z down)"
    return buckets


def hole_component_centroids(face_buffer: np.ndarray, hole_mask: np.ndarray,
                             scene_vertices: np.ndarray, scene_faces: np.ndarray) -> list[dict]:
    """Largest hole components localized in scene (and room) coordinates.

    Room = (scene_x, scene_y, -scene_z); component centroids are averages of
    the candidate face corners under the hole mask.
    """
    from scipy import ndimage

    labels, count = ndimage.label(hole_mask)
    if count == 0:
        return []
    sizes = ndimage.sum(hole_mask, labels, range(1, count + 1))
    order = np.argsort(-sizes)[:5]
    components = []
    for rank, component_index in enumerate(order, start=1):
        select = labels == component_index + 1
        faces = face_buffer[select]
        valid = (faces >= 0) & (faces < len(scene_faces))
        faces = faces[valid]
        if not len(faces):
            continue
        corners = scene_vertices[scene_faces[faces]]
        centre = corners.mean(axis=(0, 1))
        components.append({
            "component_rank": rank,
            "hole_pixels": int(sizes[component_index]),
            "scene_xyz": [round(float(v), 3) for v in centre],
            "room_xyz": [round(float(v), 3) for v in (centre[0], centre[1], -centre[2])],
        })
    return components


def covered_fraction(photographed_path: Path, fixed_mask: np.ndarray, roi_mask: np.ndarray) -> dict:
    photographed = np.asarray(Image.open(photographed_path).convert("L")) > 128
    result = {"target_pixels": int(fixed_mask.sum()),
              "photographed_fraction": float((photographed & fixed_mask).sum() / max(fixed_mask.sum(), 1))}
    if roi_mask.any():
        pair = roi_mask & fixed_mask
        result["critical_roi_fraction"] = float((photographed & pair).sum() / max(pair.sum(), 1))
        result["critical_roi_pixels"] = int(pair.sum())
    else:
        result["critical_roi_fraction"] = None
        result["critical_roi_pixels"] = 0
    return result


def render_all(arrays, views, out_dir: Path) -> list[dict]:
    records = []
    for view in views:
        paths = [out_dir / f"{view.id}-{name}.png" for name in ["rgb", "source_id", "coverage"]]
        outputs = render_view(arrays, view.camera, FINAL, FINAL, [0, 2, 3], paths)
        records.append({"id": view.id, "passes": {name: str(path) for name, path in
                        zip(["rgb", "source_id", "coverage"], paths)},
                        "buffers": outputs})
    return records


def check_split_leaks(split_doc: dict, bake_manifest: dict, allow_leaked: bool) -> list[str]:
    """Return split frames used as bake sources; exit unless diagnosed runs."""
    split_ids = set(split_doc["validation_views_captured"]) | set(split_doc["test_views_captured"])
    bake_used = {frame.get("frame_id") for frame in bake_manifest.get("frames", [])}
    leaked = sorted(split_ids & bake_used)
    if leaked and not allow_leaked:
        sys.exit(f"split frames leaked into the candidate bake (no valid benchmark evidence): {leaked} "
                 f"(use --allow-leaked-bake only for diagnosis, never for gate claims)")
    return leaked


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="stageA split run directory")
    parser.add_argument("--glb", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--captures", type=Path, default=Path("datasets/phone/moffett"),
                        help="read-only capture directory")
    parser.add_argument("--transfer-room", default=None,
                        help="evaluate the given room's frozen transfer views instead of the pilot views")
    parser.add_argument("--allow-leaked-bake", action="store_true",
                        help="record a diagnostic-only evaluation when the bake used split frames "
                             "(results are marked benchmark_invalid, never gate evidence)")
    parser.add_argument("--max-views", type=int, default=None,
                        help="smoke-test-only cap on evaluated views; full evaluations must not use it "
                             "and gate claims require the complete frozen set")
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text())
    if args.out.exists() and (args.out / "evaluation.json").exists():
        sys.exit(f"refusing to overwrite existing evaluation {args.out}")
    args.out.mkdir(parents=True, exist_ok=True)
    if not args.glb.is_file():
        sys.exit(f"missing candidate GLB: {args.glb}")

    split_doc = load_splits(args.run)
    capture_dir = args.captures / CAPTURES[split_doc["capture"]]
    if not capture_dir.is_dir():
        sys.exit(f"missing capture directory: {capture_dir}")
    views = build_views(args.run, args.captures) if args.transfer_room is None \
        else build_transfer_views(args.run, args.transfer_room)
    if not views:
        sys.exit("no frozen views; refusing to evaluate empty evidence")
    if args.max_views is not None and args.max_views < len(views):
        views = views[:args.max_views]
        print(f"SMOKE SUBSET: evaluating first {args.max_views} of {len(views)} frozen views; "
              f"results are diagnostic and never gate evidence", flush=True)
    for view in views:
        if view.reference is not None:
            Image.fromarray(view.reference.astype(np.uint8)).save(args.out / f"{view.id}-reference-500.png")
    bake_manifest_path = args.glb.with_suffix(".json")
    bake_manifest = json.loads(bake_manifest_path.read_text()) if bake_manifest_path.is_file() else {}
    leaked = check_split_leaks(split_doc, bake_manifest, args.allow_leaked_bake)

    region_doc = (args.run / "stageA" / "region.json")
    if not region_doc.is_file():
        sys.exit(f"missing frozen region definition: {region_doc} (critical ROI is a mandatory gate; "
                 f"its absence is fail/pending, never null)")
    region_box = json.loads(region_doc.read_text())["region_box"]

    arrays = scene_arrays(args.glb)
    glb_hash = digest(args.glb)
    c2r = capture_to_room_from_payload(json.loads((capture_dir / "room.json").read_text()))
    measured = measured_arrays(capture_dir, c2r)
    np.savez_compressed(args.out / "fixed-measured-arrays.npz",
                        vertices=measured[0], triangles=measured[1])
    source_pose_hash = digest(capture_dir / "poses.json")
    source_mesh_hash = digest(capture_dir / "lidar-mesh.json")

    records = render_all(arrays, views, args.out)
    tex_dim_values = sorted({(arrays[4][i].shape[1], arrays[4][i].shape[0])
                             for i in range(len(arrays[4])) if arrays[4][i] is not None})
    results = {"label": args.label, "glb": str(args.glb), "glb_sha256": glb_hash,
               "policy_sha256": digest(args.policy),
               "source_poses_sha256": source_pose_hash,
               "source_mesh_sha256": source_mesh_hash,
               "texture_dimensions_wh": tex_dim_values,
               "texture_count": len([i for i in range(len(arrays[4])) if arrays[4][i] is not None]),
               "split_file": str(args.run / "stageA" /
                                 ("splits-v2.json" if (args.run / "stageA" / "splits-v2.json").is_file()
                                  else "splits.json")),
               "bake_manifest": str(bake_manifest_path) if bake_manifest else None,
               "bake_excluded_frames": bake_manifest.get("excluded_frames", []),
               "bake_leaked_split_frames": leaked,
               "benchmark_invalid_leaked_sources": bool(leaked),
               "views": [], "gates": {}}
    coverage_gate = policy["visual_gates"]["coverage"]
    resolution_gate = policy["visual_gates"]["effective_resolution"]
    for view, record in zip(views, records):
        results["views"].append(
            evaluate_view(view, record, arrays, measured, region_box, args.out))

    results["gates"] = gate_summary(results, coverage_gate, resolution_gate)
    (args.out / "evaluation.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results["gates"], indent=1))


def evaluate_view(view: FrozenView, record: dict, arrays: tuple, measured: tuple,
                  region_box: list, out_dir: Path) -> dict:
    fixed_mask, roi_mask, mask_math = render_measured_masks(measured, view, out_dir, region_box)
    entry = {"id": view.id, "kind": "captured" if view.reference is not None else "novel",
             "width": view.width, "height": view.height,
             "reference": view.reference is not None, "passes": record["passes"],
             "fixed_measured": mask_math}
    entry["coverage"] = covered_fraction(Path(record["passes"]["coverage"]), fixed_mask, roi_mask)
    entry["holes"], untextured = hole_metrics(Path(record["passes"]["coverage"]), fixed_mask)
    Image.fromarray(untextured.astype(np.uint8) * 255, "L").save(out_dir / f"{view.id}-holes.png")
    coverage_buffer = record["buffers"][2]
    entry["hole_surfaces"] = hole_surface_decomposition(
        coverage_buffer["face_buffer"], untextured, arrays[0], arrays[1])
    entry["hole_components"] = hole_component_centroids(
        coverage_buffer["face_buffer"], untextured, arrays[0], arrays[1])
    rgb_buffer = record["buffers"][0]
    tex_dims = {i: arrays[4][i].shape[:2] for i in range(len(arrays[4])) if arrays[4][i] is not None}
    weaker, stronger, mip, valid = weaker_axis_resolution(
        rgb_buffer["uv"], rgb_buffer["texi"], rgb_buffer["depth"], tex_dims)
    entry["resolution"] = resolution_gate_stats(weaker, stronger, mip, valid, roi_mask)
    scale = min(65535 / max(weaker.max(), 1e-9), 64.0)
    Image.fromarray(np.clip(weaker * scale, 0, 65535).astype(np.uint16)).save(
        out_dir / f"{view.id}-weaker-resolution-16.png")
    Image.fromarray(mip.astype(np.uint8) * 32, "L").save(out_dir / f"{view.id}-active-mip.png")
    if view.reference is not None:
        candidate = np.asarray(
            Image.open(Path(record["passes"]["rgb"])).convert("RGB"), dtype=np.float64)
        entry["psnr_ssim_fixed_mask"] = psnr_ssim(view.reference, candidate, fixed_mask)
        entry["edge_profiles"], _ = edge_width_profiles(
            view.reference, candidate, None, out_dir / f"{view.id}-frozen-edges.json")
        entry["feature_matches"] = feature_matches(view.reference, candidate)
    return entry


def gate_summary(results: dict, coverage_gate: dict, resolution_gate: dict) -> dict:
    photographed = {v["id"]: v["coverage"]["photographed_fraction"] for v in results["views"]}
    roi = {v["id"]: v["coverage"]["critical_roi_fraction"] for v in results["views"]}
    holes = {v["id"]: v["holes"]["largest_hole_fraction"] for v in results["views"]}
    resolution = {v["id"]: v["resolution"]["fraction_meeting_both_1"] for v in results["views"]}
    return {
        "thresholds": json.loads(json.dumps({
            "coverage_min_target_fraction": coverage_gate["min_photographed_target_pixel_fraction_each_view"],
            "critical_roi_coverage_min": coverage_gate["min_photographed_critical_roi_fraction_each"],
            "largest_hole_fraction_max": coverage_gate["max_single_untextured_hole_target_pixel_fraction"],
            "resolution_fraction_min": resolution_gate["min_fraction_supported_target_pixels_meeting_both"],
            "critical_roi_source_linear_min": resolution_gate["critical_roi_target_samples_per_output_pixel_linear"],
        })),
        "denominator": "fixed full-resolution measured geometry at 500 x 500, candidate-independent",
        "per_view_photographed_fraction": {k: (None if v != v else round(v, 6)) for k, v in photographed.items()},
        "per_view_critical_roi_fraction": {k: (None if v is None or v != v else round(v, 6)) for k, v in roi.items()},
        "per_view_largest_hole_fraction": {k: round(v, 6) for k, v in holes.items()},
        "per_view_resolution_fraction_meeting_both": {k: (None if v != v else round(v, 6)) for k, v in resolution.items()},
        "manual_visual_review": ("pending_agent_review; this dispatch endpoint has no image input, "
                                 "so no agent visual review was performed and none is claimed"),
        "machine_metrics_only": True,
        "evaluator_notes": "caller-supplied pass booleans are never evidence; thresholds from the policy at run time",
    }


if __name__ == "__main__":
    main()
