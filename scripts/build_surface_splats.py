"""Deterministic surface-Gaussian build on the allowed pilot photo set.

Colour and support come only from the frozen training photo allowlist (capture
ID + frame ID + source RGB SHA-256).  The script refuses held-out, extra or
duplicate inputs before any image is decoded; camera calibration comes from the
prepared dataset's per-frame intrinsics; unsupported samples are pruned, never
tinted neutral grey.

Outputs are namespaced per configuration, and nothing is ever overwritten or
published; the existing captures, manifests and prior export directories are
never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image
from standardphysics_pipeline.ingest import capture_to_room_from_payload
from standardphysics_pipeline.render_efficiency import build_surface_gaussians
from standardphysics_pipeline.render_efficiency.allowlist import (
    AllowlistError,
    camera_from_transforms,
    load_allowlist,
    reject_heldout_entries,
)
from standardphysics_pipeline.textures.scan_colour import (
    colour_the_scan,
    scan_geometry,
    unused_vertices_removed,
)

CAPTURE_CENTRE = "454B3661-D3F8-46E8-ADAA-3123E759AA64"


def _sha256(path: Path) -> str:
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def load_static_mask(path: Path) -> np.ndarray:
    with Image.open(path) as opened:
        grey = np.asarray(opened.convert("L"), dtype=np.float32) / 255.0
    return grey


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--captures", type=Path, default=Path("datasets/phone/moffett"),
    )
    parser.add_argument(
        "--dataset", type=Path, default=Path("runs/moffett/render-efficiency/r001/pilot-dataset-v2"),
        help="prepared 1280px pilot dataset with transforms.json, images/ and masks/",
    )
    parser.add_argument(
        "--manifest", type=Path,
        default=Path("runs/moffett/render-efficiency/r001/pilot-manifest-v2.json"),
        help="frozen train/validation/test split manifest",
    )
    parser.add_argument(
        "--allowlist", type=Path, required=True,
        help="frozen training input allowlist JSON with capture+frame identity and RGB SHA-256",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("runs/moffett/render-efficiency/r003/candidate"),
    )
    parser.add_argument("--spacing", type=float, default=0.02)
    parser.add_argument("--max-samples", type=int, default=150000)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--opacity", type=float, default=0.5)
    parser.add_argument("--tangent-factor", type=float, default=0.75)
    parser.add_argument("--normal-ratio", type=float, default=0.1)
    args = parser.parse_args()

    started = time.monotonic()
    entries = load_allowlist(args.allowlist)
    reject_heldout_entries(entries, args.manifest)

    transforms = json.loads((args.dataset / "transforms.json").read_text())
    mask_dir = args.dataset / "masks"

    capture_dir = args.captures / CAPTURE_CENTRE
    c2r = capture_to_room_from_payload(json.loads((capture_dir / "room.json").read_text()))
    vertices, triangles = scan_geometry(capture_dir / "lidar-mesh.json", c2r)

    cameras, images, masks = [], [], []
    # Allowlist validation happens before any file is opened; a mismatching
    # digest aborts the build (policy D02).
    for entry in entries:
        frame_path = args.dataset / entry["file_path"]
        if not frame_path.is_file():
            raise AllowlistError(f"missing allowed input: {frame_path}")
        digest = _sha256(frame_path)
        if digest != entry["rgb_sha256"]:
            raise AllowlistError(
                f"source bytes changed for {entry['frame_id']}: "
                f"{digest[:16]} != {entry['rgb_sha256'][:16]}"
            )
    for entry in entries:
        frame_path = args.dataset / entry["file_path"]
        frames = [f for f in transforms["frames"] if f["file_path"] == entry["file_path"]]
        if len(frames) != 1:
            raise AllowlistError(f"expected one prepared transform for {entry['frame_id']}, got {len(frames)}")
        cameras.append(camera_from_transforms(frames[0]))
        with Image.open(frame_path) as opened:
            images.append(np.asarray(opened.convert("RGB"), dtype=np.float32) / 255.0)
        masks.append(load_static_mask(mask_dir / f"{frame_path.stem}.png"))

    coloured = colour_the_scan(vertices, triangles, cameras, images, masks=masks)
    coloured = unused_vertices_removed(coloured)

    result = build_surface_gaussians(
        coloured.vertices,
        coloured.triangles,
        coloured.colours,
        spacing=args.spacing,
        tangent_factor=args.tangent_factor,
        normal_ratio=args.normal_ratio,
        opacity=args.opacity,
        seed=args.seed,
        max_samples=args.max_samples,
        seen=coloured.seen,
        source_ids=coloured.sources,
    )

    if args.output.exists():
        raise FileExistsError(f"refusing existing output destination: {args.output}")
    args.output.mkdir(parents=True, exist_ok=False)
    ply_path = args.output / "surface-splats.ply"
    result.gaussians.export_ply(ply_path)

    per_source = Counter(
        source for state in result.sample_states for source in state.source_ids
    )
    provenance = {
        "policy": "docs/research/deepseek-lidar-experiment-policy-v2.json",
        "method": "deterministic_surface_gaussians",
        "colour_source": "allowed train RGB with static-mask support",
        "input_allowlist_sha256": _sha256(args.allowlist),
        "manifest_sha256": _sha256(args.manifest),
        "dataset_transforms_sha256": _sha256(args.dataset / "transforms.json"),
        "mesh_sha256": _sha256(capture_dir / "lidar-mesh.json"),
        "room_sha256": _sha256(capture_dir / "room.json"),
        "valence_edges": {
            "allowed_train_count": len(entries),
            "decoded_count": len(images),
            "allowed_identities": [
                {k: e[k] for k in ("capture_id", "frame_id", "file_path", "rgb_sha256")}
                for e in entries
            ],
        },
        "config": {
            "seed": args.seed,
            "spacing_m": args.spacing,
            "max_samples": args.max_samples,
            "opacity": args.opacity,
            "tangent_factor": args.tangent_factor,
            "normal_ratio": args.normal_ratio,
        },
        "support": {
            "mesh_vertices": int(len(coloured.vertices)),
            "painted_fraction": float(coloured.painted_fraction),
            "supported_samples": result.supported_count,
            "rejected_degenerate": result.rejected_count,
            "pruned_unseen_samples": result.unsupported_count,
            "per_source_sample_counts": dict(sorted(per_source.items())),
        },
        "output_sha256": _sha256(ply_path),
        "output_path": str(ply_path),
        "wall_seconds": round(time.monotonic() - started, 3),
    }
    (args.output / "provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()
