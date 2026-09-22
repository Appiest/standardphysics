"""Freeze benchmark moffett-photo-mesh-500 split v2 for one captured room.

Writes splits-v2.json, exclude-frames-v2.txt and a receipt binding input
hashes to the selection code. Never modifies the capture or prior runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from standardphysics_pipeline.ingest import capture_to_room_from_payload  # noqa: E402
from standardphysics_pipeline.render_efficiency.splits import (  # noqa: E402
    SELECTION_SEPARATION,
    freeze_splits_from_docs,
)
from standardphysics_pipeline.textures.camera import load_cameras  # noqa: E402

CAPTURES = {
    "center": "454B3661-D3F8-46E8-ADAA-3123E759AA64",
    "top": "BBB88E0A-A2C3-4611-AFE1-71C43454E8C6",
    "bottom_left": "D9946491-26FD-4864-9673-C88180328109",
    "left": "A92A8ED6-87A5-474D-8514-D2D0E6A1F00D",
}

EXPOSED_VALIDATION_CENTER = [
    "frame-0086", "frame-0095", "frame-0047", "frame-0114", "frame-0053", "frame-0018",
    "frame-0088", "frame-0081", "frame-0083",
]
EXPOSED_NON_SPLIT_CENTER = {"frame-0035", "frame-0038"}
PRIOR_EXCLUDE_FRAMES = [
    "frame-0018", "frame-0039", "frame-0047", "frame-0053", "frame-0081", "frame-0083",
    "frame-0086", "frame-0088", "frame-0090", "frame-0092", "frame-0095", "frame-0114",
]

MIN_SHARPNESS = 6.0
"""Laplacian mean on a 480-px-side greyscale downsample; a weak blur filter
applied before the freeze so obviously unsharp frames do not waste test slots.
Recorded in the manifest; threshold documented, not tuned after selection."""


def digest(path: Path) -> str:
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def sharpness(path: Path) -> float:
    import cv2

    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    height, width = image.shape
    side = min(height, width)
    left, top = (width - side) // 2, (height - side) // 2
    thumb = image[top:top + side, left:left + side]
    thumb = cv2.resize(thumb, (480, 480))
    return float(cv2.Laplacian(thumb, cv2.CV_64F).var() ** 0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("room", choices=CAPTURES)
    parser.add_argument("--captures", type=Path, default=Path("datasets/phone/moffett"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--validation-frames", nargs="+", default=EXPOSED_VALIDATION_CENTER,
                        metavar="FRAME_ID")
    parser.add_argument("--exposed-non-split-frames", nargs="+", default=sorted(EXPOSED_NON_SPLIT_CENTER),
                        metavar="FRAME_ID")
    parser.add_argument("--test-count", type=int, default=6)
    parser.add_argument("--min-sharpness", type=float, default=MIN_SHARPNESS)
    parser.add_argument("--novel-views-source", type=Path, default=None,
                        help="existing splits file whose frozen novel view specs are reused")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.test_count < 1 or args.min_sharpness < 0:
        parser.error("test-count must be positive and min-sharpness non-negative")

    directory = args.captures / CAPTURES[args.room]
    poses_path = directory / "poses.json"
    c2r = capture_to_room_from_payload(json.loads((directory / "room.json").read_text()))
    poses = json.loads(poses_path.read_text())
    frames = {p["frame_id"]: directory / "frames" / Path(p["image"]).name for p in poses}
    cameras = load_cameras(poses_path, frames, c2r)
    positions = {c.frame_id: (c.position, c.forward) for c in cameras}
    selected_sharpness = {}
    pool = [fid for fid in frames if fid not in args.validation_frames
            and fid not in args.exposed_non_split_frames]
    for fid in pool:
        value = sharpness(frames[fid])
        if value < args.min_sharpness:
            continue
        selected_sharpness[fid] = value

    eligible_ids = set(selected_sharpness)
    if args.dry_run:
        print("eligible frame count:", len(eligible_ids), "of", len(poses))
        frozen = freeze_splits_from_docs(
            poses, args.validation_frames, set(args.exposed_non_split_frames),
            {"frame_count": args.test_count}, positions, eligible_ids)
        print("test candidates:", frozen["test_views_captured"])
        return

    args.out.mkdir(parents=True, exist_ok=False)
    frozen = freeze_splits_from_docs(
        poses, args.validation_frames, set(args.exposed_non_split_frames),
        {"frame_count": args.test_count}, positions, eligible_ids)
    if len(frozen["test_views_captured"]) < args.test_count:
        raise RuntimeError(
            f"only {len(frozen['test_views_captured'])} separated test frames available; "
            f"need {args.test_count}; recorded sharpness pool {len(selected_sharpness)}")
    frozen["capture"] = args.room
    frozen["capture_id"] = CAPTURES[args.room]
    sharpness_record = {}
    for fid in frozen["validation_views_captured"] + frozen["test_views_captured"]:
        value = selected_sharpness.get(fid)
        sharpness_record[fid] = value if value is not None else sharpness(frames[fid])
    frozen["sharpness"] = sharpness_record
    if args.novel_views_source is not None:
        source = json.loads(args.novel_views_source.read_text())
        if not source.get("novel_views"):
            raise ValueError("novel-views source has no frozen novel views")
        frozen["novel_views"] = source["novel_views"]
        frozen["novel_views_provenance"] = str(args.novel_views_source.resolve())
        frozen["novel_views_note"] = ("view definitions (eye height 1.15m, 60deg vertical FOV, 500px) "
                                      "frozen by the original split; camera specs do not leak bake frames")
    frozen["prior_exclude_frames_context"] = sorted(PRIOR_EXCLUDE_FRAMES)
    frozen["poses_sha256"] = digest(poses_path)
    frozen["source_poses_path"] = str(poses_path.resolve())
    frozen["selection_min_sharpness"] = args.min_sharpness
    frozen["selection_note"] = ("sharpness filter runs before the freeze on the unselected "
                                "pool; selected frames are then untouched and held-out-from-bake")
    exclude = sorted(set(frozen["validation_views_captured"]) | set(frozen["test_views_captured"]))
    splits_path = args.out / "splits-v2.json"
    splits_path.write_text(json.dumps(frozen, indent=2) + "\n")
    exclude_path = args.out / "exclude-frames-v2.txt"
    exclude_path.write_text("\n".join(exclude) + "\n")
    receipt = {
        "run": "moffett-photo-mesh-500 frozen split v2",
        "splits_sha256": digest(splits_path),
        "exclude_sha256": digest(exclude_path),
        "poses_sha256": frozen["poses_sha256"],
        "selection_code_sha256": digest(Path(__file__).parent.parent / "packages" / "pipeline" /
                                        "standardphysics_pipeline" / "render_efficiency" / "splits.py"),
        "separation": SELECTION_SEPARATION,
        "excluded_frame_ids": exclude,
        "split_v1_reference": "runs/moffett/photo-mesh-500/r001/stageA/splits.json (original checkout, preserved)",
    }
    (args.out / "split-freeze-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("frozen", len(exclude), "excluded frames;", len(frozen["test_views_captured"]),
          "untouched test frames;", args.out)


if __name__ == "__main__":
    main()
