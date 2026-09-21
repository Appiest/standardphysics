"""Find independently repeatable floor alignments from captured image features.

Writes candidates and correspondence evidence only; never changes the live scan.
Image features are lifted into each capture's metric LiDAR frame before fitting.
"""
from __future__ import annotations

import argparse
import itertools
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial import cKDTree
from standardphysics_pipeline.ingest import capture_to_room_from_payload
from standardphysics_pipeline.textures.camera import load_cameras
from standardphysics_pipeline.textures.scan_colour import scan_geometry

CAPTURES = {
    "center": "454B3661-D3F8-46E8-ADAA-3123E759AA64",
    "top": "BBB88E0A-A2C3-4611-AFE1-71C43454E8C6",
    "bottom_left": "D9946491-26FD-4864-9673-C88180328109",
    "left": "A92A8ED6-87A5-474D-8514-D2D0E6A1F00D",
}


def image_features(root: Path, output: Path, name: str, count: int):
    directory = root / CAPTURES[name]
    cache = output / f"{name}_features_{count}_v4.npz"
    if cache.exists():
        data = np.load(cache)
        return [{key: data[f"{i}_{key}"] for key in ("desc", "xyz", "pixels", "frame")}
                for i in range(int(data["count"]))]
    transform = capture_to_room_from_payload(json.loads((directory / "room.json").read_text()))
    geometry = output / f"{name}_geometry.npz"
    if geometry.exists():
        vertices = np.load(geometry)["vertices"]
    else:
        vertices, triangles = scan_geometry(directory / "lidar-mesh.json", transform)
        np.savez_compressed(geometry, vertices=vertices, triangles=triangles)
    poses = json.loads((directory / "poses.json").read_text())
    frames = {p["frame_id"]: directory / "frames" / Path(p["image"]).name for p in poses}
    cameras = load_cameras(directory / "poses.json", frames, transform)
    sift = cv2.SIFT_create(nfeatures=3000, contrastThreshold=.025)
    result = []
    for index in np.linspace(0, len(cameras) - 1, min(count, len(cameras)), dtype=int):
        camera = cameras[index]
        image = cv2.imread(str(frames[camera.frame_id]), cv2.IMREAD_GRAYSCALE)
        factor = min(1., 960 / max(image.shape))
        image = cv2.resize(image, (round(image.shape[1]*factor), round(image.shape[0]*factor)))
        camera = camera.resized(image.shape[1], image.shape[0])
        keypoints, descriptors = sift.detectAndCompute(image, None)
        if descriptors is None:
            continue
        pixels = np.array([p.pt for p in keypoints])
        u, v, depth = camera.project(vertices)
        valid = (depth > .25) & (depth < 12) & (u >= 0) & (u < camera.width-1) & (v >= 0) & (v < camera.height-1)
        ids = np.flatnonzero(valid)
        if len(ids) == 0:
            continue
        # Keep the front surface at every sensor pixel, never lift through furniture.
        pixel_index = np.rint(v[ids]).astype(int)*camera.width + np.rint(u[ids]).astype(int)
        ordered = np.argsort(depth[ids], kind="stable")
        _, first = np.unique(pixel_index[ordered], return_index=True)
        visible = ids[ordered[first]]
        distances, nearest = cKDTree(np.column_stack((u[visible], v[visible]))).query(pixels)
        selected = visible[nearest]
        keep = (distances < 5.) & (vertices[selected, 2] > -.15) & (vertices[selected, 2] < 3.5)
        result.append({"desc": descriptors[keep], "xyz": vertices[selected[keep]],
                       "pixels": pixels[keep], "frame": np.array(camera.frame_id)})
    np.savez_compressed(cache, count=len(result), **{f"{i}_{k}": v for i, f in enumerate(result) for k, v in f.items()})
    print(name, "frames", len(result), "lifted features", sum(len(f["xyz"]) for f in result), flush=True)
    return result


def rigid_fit(source, target):
    a, b = source[:, :2].mean(0), target[:, :2].mean(0)
    u, _, vt = np.linalg.svd((source[:, :2]-a).T @ (target[:, :2]-b))
    r = vt.T @ u.T
    if np.linalg.det(r) < 0:
        vt[-1] *= -1
        r = vt.T @ u.T
    transform = np.eye(4)
    transform[:2, :2] = r
    transform[:2, 3] = b-r@a
    transform[2, 3] = np.median(target[:, 2]-source[:, 2])
    return transform


def match_pair(source, target):
    if min(len(source["desc"]), len(target["desc"])) < 12:
        return None
    matcher = cv2.FlannBasedMatcher(dict(algorithm=1, trees=4), dict(checks=64))
    matches = matcher.knnMatch(source["desc"], target["desc"], k=2)
    good = [pair[0] for pair in matches if len(pair) == 2 and pair[0].distance < .72*pair[1].distance]
    # Several source keypoints matching one target do not supply independent evidence.
    unique = {}
    for match in good:
        if match.trainIdx not in unique or match.distance < unique[match.trainIdx].distance:
            unique[match.trainIdx] = match
    good = list(unique.values())
    if len(good) < 8:
        return None
    a = source["xyz"][[m.queryIdx for m in good]]
    b = target["xyz"][[m.trainIdx for m in good]]
    matrix, mask = cv2.estimateAffinePartial2D(np.ascontiguousarray(a[:, :2]), np.ascontiguousarray(b[:, :2]), method=cv2.RANSAC,
                                             ransacReprojThreshold=.18, maxIters=3000, confidence=.999)
    if matrix is None or abs(np.linalg.norm(matrix[:, 0])-1) > .04:
        return None
    selected = mask.ravel().astype(bool)
    if selected.sum() < 6:
        return None
    t = rigid_fit(a[selected], b[selected])
    errors = np.linalg.norm(a@t[:3, :3].T+t[:3, 3]-b, axis=1)
    selected &= errors < .22
    if selected.sum() < 6 or np.linalg.norm(np.ptp(a[selected, :2], axis=0)) < 1.:
        return None
    t = rigid_fit(a[selected], b[selected])
    errors = np.linalg.norm(a[selected]@t[:3, :3].T+t[:3, 3]-b[selected], axis=1)
    return {"source_frame": str(source["frame"]), "target_frame": str(target["frame"]),
            "inliers": int(selected.sum()), "rmse_m": float(np.sqrt(np.mean(errors**2))),
            "transform": t.tolist(), "source_points": a[selected].tolist(), "target_points": b[selected].tolist()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", type=Path, default=Path("datasets/phone/moffett"))
    parser.add_argument("--output", type=Path, default=Path("runs/moffett"))
    parser.add_argument("--frames", type=int, default=48)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cv2.setNumThreads(2)
    features = {name: image_features(args.captures, args.output, name, args.frames) for name in CAPTURES}
    for source, target in itertools.combinations(CAPTURES, 2):
        path = args.output / f"matches_{source}_to_{target}_{args.frames}_v4.json"
        if path.exists():
            continue
        pairs = itertools.product(features[source], features[target])
        with ThreadPoolExecutor(max_workers=4) as pool:
            candidates = [m for m in pool.map(lambda pair: match_pair(*pair), pairs) if m is not None]
        candidates.sort(key=lambda m: m["inliers"], reverse=True)
        path.write_text(json.dumps(candidates, indent=2)+"\n")
        print(source, "to", target, len(candidates), "candidate frame pairs", flush=True)
        for m in candidates[:4]:
            t = np.array(m["transform"])
            print(m["source_frame"], m["target_frame"], m["inliers"], round(m["rmse_m"], 3),
                  "yaw", round(float(np.degrees(np.arctan2(t[1,0], t[0,0]))), 2), "xyz", t[:3,3].round(3), flush=True)


if __name__ == "__main__":
    main()
