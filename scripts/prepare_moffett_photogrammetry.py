"""Select sharp, temporally distributed original frames without altering pixels."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import cv2
import numpy as np
from moffett_image_registration import CAPTURES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room", choices=[*CAPTURES, "all"], default="all")
    parser.add_argument("--maximum-images", type=int, default=1500)
    parser.add_argument("--captures", type=Path, default=Path("datasets/phone/moffett"))
    parser.add_argument("--output", type=Path, default=Path("runs/moffett/photogrammetry-input"))
    args = parser.parse_args()
    if args.maximum_images < 4:
        parser.error("maximum-images must be at least 4")
    names = list(CAPTURES) if args.room == "all" else [args.room]
    output = args.output
    images = output/"images"
    if images.exists() and any(images.iterdir()):
        parser.error("use a new output directory; existing reconstruction input is immutable")
    images.mkdir(parents=True, exist_ok=True)
    cv2.setNumThreads(2)
    captures = {}
    for name in names:
        directory = args.captures/CAPTURES[name]
        poses = json.loads((directory/"poses.json").read_text())
        captures[name] = (directory, poses)
    total = sum(len(poses) for _, poses in captures.values())
    available = min(total, args.maximum_images)
    counts = {name: int(len(poses)/total*available) for name, (_, poses) in captures.items()}
    for name in names[:available-sum(counts.values())]:
        counts[name] += 1
    manifest = []
    for name, (directory, poses) in captures.items():
        groups = np.array_split(np.arange(len(poses)), counts[name])
        for group in groups:
            scores = []
            for index in group:
                pose = poses[index]
                source = directory/"frames"/Path(pose["image"]).name
                gray = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
                if gray is None:
                    raise ValueError(f"Unreadable source image: {source}")
                small = cv2.resize(gray, (640, round(gray.shape[0]*640/gray.shape[1])))
                scores.append((float(cv2.Laplacian(small, cv2.CV_32F).var()), int(index), source))
            sharpness, index, source = max(scores, key=lambda item: item[0])
            filename = f"image-{len(manifest):06d}.jpg"
            os.link(source.resolve(), images/filename)
            manifest.append({"filename": filename, "room": name, "capture_id": CAPTURES[name],
                             "frame_id": poses[index]["frame_id"], "pose_index": index,
                             "source": str(source.resolve()), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                             "sharpness_score": sharpness, "pixel_data": "unchanged original JPEG"})
        print(name, counts[name], "of", len(poses), "frames", flush=True)
    (output/"input-manifest.json").write_text(json.dumps({"frames": manifest, "ordering": "per-capture sequential; unordered across captures"}, indent=2)+"\n")


if __name__ == "__main__":
    main()
