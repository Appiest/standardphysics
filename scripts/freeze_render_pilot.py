"""Produce a reproducible Moffett pilot split for the render-efficiency benchmark.

Sorts frames by ARKit trajectory order (``pose_index``) and selects held-out
views from separated trajectory windows, so validation and test are not
adjacent video frames of the training set.  Selection inside a window is
sharpness-greedy with a minimum pose separation (never a bare time stride).

The difficult "outlet area" (poses containing the preregistered outlet frames
image-000011/12/15) deliberately contributes to the final-test set, so the
critical ROIs stay evaluable.  A minimum pose gap of ``margin`` separates every
held-out window from the training pool.

The result is deterministic and reproducible; the rendered near-duplicate and
separation review is recorded separately (still required, not claimed here).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _sha256(path: Path) -> str:
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def _greedy_sharp(entries, count: int, min_separation: int) -> list[dict]:
    """Sharpest frames first, skipping any frame too close in pose to a pick."""
    ordered = sorted(entries, key=lambda e: (-e["sharpness"], e["pose_index"]))
    picked = []
    poses = []
    for entry in ordered:
        if all(abs(entry["pose_index"] - p) >= min_separation for p in poses):
            picked.append(entry)
            poses.append(entry["pose_index"])
        if len(picked) == count:
            break
    return picked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prepared", type=Path, default=Path("runs/moffett/splat-datasets/center"))
    parser.add_argument("output", type=Path, default=Path("runs/moffett/render-efficiency/r001/pilot-manifest.json"))
    parser.add_argument("--train", type=int, default=32)
    parser.add_argument("--validation", type=int, default=8)
    parser.add_argument("--test", type=int, default=8)
    parser.add_argument("--margin", type=int, default=20, help="minimum pose gap between any two groups")
    parser.add_argument("--min-separation", type=int, default=8, help="minimum pose gap inside a group")
    parser.add_argument("--seed", type=int, default=1729)
    args = parser.parse_args()

    transforms = json.loads((args.prepared / "transforms.json").read_text())
    provenance = json.loads((args.prepared / "provenance.json").read_text())
    frames = transforms["frames"]
    by_file = {entry["filename"]: entry for entry in provenance["frames"]}
    if len(frames) != len(by_file):
        raise SystemExit("transforms.json and provenance.json frame counts disagree")

    ordered = []
    for frame in frames:
        name = frame["file_path"].split("/")[-1]
        meta = by_file[name]
        ordered.append({
            "file_path": frame["file_path"],
            "mask_path": frame["mask_path"],
            "pose_index": meta["pose_index"],
            "sharpness": meta["sharpness_score"],
            "frame_id": meta["frame_id"],
        })
    ordered.sort(key=lambda e: e["pose_index"])

    outlet_files = {"image-000011.jpg", "image-000012.jpg", "image-000015.jpg"}
    outlet_picks = [e for e in ordered if e["file_path"].endswith(tuple(outlet_files))]

    def in_band(e, low, high):
        return low <= e["pose_index"] <= high

    tail_test = _greedy_sharp([e for e in ordered if in_band(e, 800, 940)], 4, args.min_separation)
    outlet_test = outlet_picks + _greedy_sharp(
        [e for e in ordered if in_band(e, 25, 90) and e not in outlet_picks], 1, args.min_separation
    )
    validation = _greedy_sharp([e for e in ordered if in_band(e, 400, 540)], 8, args.min_separation)

    blocked = (
        [(25 - args.margin, 90 + args.margin), (800 - args.margin, 940), (400 - args.margin, 540 + args.margin)]
    )
    reserved_poses = {e["pose_index"] for e in outlet_test + tail_test + validation}

    def blocked_pose(entry):
        return any(low <= entry["pose_index"] <= high for low, high in blocked)

    train_pool = [
        e for e in ordered
        if not blocked_pose(e)
        and e["pose_index"] not in reserved_poses
        and e not in outlet_picks
    ]
    train = _greedy_sharp(train_pool, args.train, args.min_separation)

    all_groups = {
        "train": train,
        "validation": validation,
        "test": sorted(outlet_test + tail_test, key=lambda e: e["pose_index"]),
    }
    if not all(len(g) >= 1 for g in all_groups.values()):
        raise SystemExit("split produced an empty group; not enough separated frames")
    paths = {}
    for key, group in all_groups.items():
        identifiers = [e["file_path"] for e in group]
        if len(set(identifiers)) != len(identifiers):
            raise SystemExit(f"{key} group is not pairwise file-unique")
        paths[key] = identifiers
    if set(paths["train"]) & set(paths["validation"]) or set(paths["train"]) & set(paths["test"]) \
            or set(paths["validation"]) & set(paths["test"]):
        raise SystemExit("split groups are not disjoint")

    def min_gap(left, right):
        return min(abs(l["pose_index"] - r["pose_index"]) for l in left for r in right)

    manifest = {
        "policy": "docs/archive/research/deepseek-lidar-experiment-policy.json",
        "seed": args.seed,
        "version": 2,
        "split_unit": "ARKit pose_index trajectory order",
        "selection": "sharpness-greedy with minimum pose separation inside separated trajectory windows",
        "outlet_area": "poses 25-90 with outlet frames image-000011/12/15 placed in final test",
        "group_min_pose_gaps": {
            "train_to_validation": min_gap(train, validation),
            "train_to_test": min_gap(train, all_groups["test"]),
            "validation_to_test": min_gap(validation, all_groups["test"]),
        },
        "split_independence_reviewed": False,
        "split_independence_note": "separated windows guaranteed geometrically; near-duplicate grouping and the visual review are recorded by the split-review agent",
        "targets": {"train": args.train, "validation": args.validation, "test": args.test},
        "achieved": {
            "train": len(train), "validation": len(validation), "test": len(all_groups["test"]),
        },
        "splits": {
            "train": [{"file_path": e["file_path"], "mask_path": e["mask_path"], "pose_index": e["pose_index"], "frame_id": e["frame_id"]} for e in train],
            "validation": [{"file_path": e["file_path"], "mask_path": e["mask_path"], "pose_index": e["pose_index"], "frame_id": e["frame_id"]} for e in validation],
            "test": [{"file_path": e["file_path"], "mask_path": e["mask_path"], "pose_index": e["pose_index"], "frame_id": e["frame_id"]} for e in all_groups["test"]],
        },
        "input_hashes": {
            "transforms_sha256": _sha256(args.prepared / "transforms.json"),
            "provenance_sha256": _sha256(args.prepared / "provenance.json"),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({
        "achieved": manifest["achieved"],
        "group_min_pose_gaps": manifest["group_min_pose_gaps"],
        "outlet_frames_in_test": [e["file_path"] for e in outlet_test],
        "output": str(args.output),
    }, indent=2))


if __name__ == "__main__":
    main()
