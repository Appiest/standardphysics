"""Offline photographic surface bake of one preserved Moffett capture.

Outputs a local-room GLB and coverage manifest. This does not publish an
unverified alignment or modify the original measured geometry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import photo_mesh_glb
import trimesh
from moffett_image_registration import CAPTURES
from PIL import Image
from standardphysics_pipeline.ingest import capture_to_room_from_payload
from standardphysics_pipeline.textures.camera import load_cameras
from standardphysics_pipeline.textures.scan_colour import scan_geometry
from standardphysics_pipeline.textures.surface_photos import (
    choose_views,
    choose_views_partial,
    region_weighted_decimate,
    snap_to_measured,
    subdivide_masked_faces,
)


def code_key() -> str:
    """Cache identity for the display-mesh path: any code change invalidates it."""
    import photo_mesh_glb
    import standardphysics_pipeline.textures.scan_colour as scan_colour
    import standardphysics_pipeline.textures.surface_photos as surface_photos

    parts = [Path(__file__).read_bytes(), Path(scan_colour.__file__).read_bytes(),
             Path(surface_photos.__file__).read_bytes(), Path(photo_mesh_glb.__file__).read_bytes()]
    return hashlib.sha256(b"".join(parts)).hexdigest()[:12]


def excluded_frames_from_args(args) -> tuple[set[str], dict | None]:
    excluded = set(args.exclude_frame)
    if args.exclude_from is not None:
        excluded |= {line.strip() for line in args.exclude_from.read_text().splitlines() if line.strip()}
    split_block = None
    if args.split_manifest is not None:
        split_doc = json.loads(args.split_manifest.read_text())
        split_frames = set(split_doc.get("validation_views_captured", [])) | \
            set(split_doc.get("test_views_captured", []))
        if not split_frames:
            raise ValueError("split manifest contains no validation or test frames")
        excluded |= split_frames
        split_block = {"path": str(args.split_manifest.resolve()),
                       "sha256": hashlib.file_digest(args.split_manifest.open("rb"), "sha256").hexdigest(),
                       "validation_views": sorted(split_doc.get("validation_views_captured", [])),
                       "test_views": sorted(split_doc.get("test_views_captured", [])),
                       "split_version": split_doc.get("selection", {}).get("split_version")}
    return excluded, split_block


def region_faces_of(vertices, triangles, region_from: Path | None,
                    face_fraction: float) -> tuple[np.ndarray | None, dict | None]:
    region_block = None
    if region_from is None:
        return None, region_block
    region_block = {"path": str(region_from.resolve()),
                    "sha256": hashlib.file_digest(region_from.open("rb"), "sha256").hexdigest(),
                    "region_face_fraction": face_fraction}
    region_doc = json.loads(region_from.read_text())
    room_lo = np.asarray(region_doc["region_box"][0], dtype=np.float64)
    room_hi = np.asarray(region_doc["region_box"][1], dtype=np.float64)
    inside = ((vertices[:, 0] >= room_lo[0]) & (vertices[:, 0] <= room_hi[0]) &
              (vertices[:, 1] >= room_lo[1]) & (vertices[:, 1] <= room_hi[1]) &
              (vertices[:, 2] >= room_lo[2]) & (vertices[:, 2] <= room_hi[2]))
    region_faces = inside[triangles].all(axis=1)
    if not region_faces.any():
        raise ValueError("region box selects no faces of the measured mesh")
    return region_faces, region_block


def display_mesh(cache: Path, vertices, triangles, args, region_faces: np.ndarray | None):
    if cache.exists():
        saved = np.load(cache)
        if region_faces is None or not args.subdivide_region or "region_mask" in saved:
            mask = saved["region_mask"] if "region_mask" in saved else None
            return saved["vertices"], saved["triangles"], mask
    if region_faces is not None:
        if args.region_face_fraction <= 0 or args.region_face_fraction >= 1:
            raise ValueError("region-face-fraction must be between 0 and 1")
        region_budget = max(int(args.faces * args.region_face_fraction), 4)
        rest_budget = max(args.faces - region_budget, 4)
        display_vertices, display_triangles, region_face_count = region_weighted_decimate(
            vertices, triangles, region_faces, region_budget, rest_budget)
        combined_mask = np.zeros(len(display_triangles), dtype=bool)
        combined_mask[:region_face_count] = True
    else:
        mesh = trimesh.Trimesh(vertices, triangles, process=False)
        reduced = mesh.simplify_quadric_decimation(face_count=args.faces)
        display_vertices, display_triangles = reduced.vertices, reduced.faces
        combined_mask = np.zeros(len(display_triangles), dtype=bool)
    np.savez_compressed(cache, vertices=display_vertices, triangles=display_triangles,
                        region_mask=combined_mask)
    return display_vertices, display_triangles, combined_mask


def source_cameras(directory, c2r, excluded_frames: set[str], camera_count: int):
    poses = json.loads((directory/"poses.json").read_text())
    frames = {p["frame_id"]: directory/"frames"/Path(p["image"]).name for p in poses}
    cameras = load_cameras(directory/"poses.json", frames, c2r)
    missing = sorted(excluded_frames - set(frames))
    if missing:
        raise ValueError(f"excluded frames missing from capture poses: {missing}")
    cameras = [camera for camera in cameras if camera.frame_id not in excluded_frames]
    if not cameras:
        raise ValueError("no source cameras remain after exclusions")
    cameras = [cameras[i] for i in np.linspace(0, len(cameras)-1, min(camera_count, len(cameras)), dtype=int)]
    return frames, cameras


def write_glb(output: Path, room: str, args, cameras, frames, display_vertices,
              display_triangles, assignment):
    builder = photo_mesh_glb.GlbBuilder()
    builder.add_neutral_material((158/255, 153/255, 148/255))
    used = []
    for index in np.unique(assignment):
        faces = display_triangles[assignment == index]
        points = display_vertices[faces.ravel()]
        geometry = np.column_stack([points[:, 0], points[:, 2], -points[:, 1]])
        if index < 0:
            builder.add_neutral_mesh(geometry, np.arange(len(points)).reshape(-1, 3), f"{room}:{index}")
            continue
        camera = cameras[index]
        image_path = frames[camera.frame_id]
        photo = Image.open(image_path).convert("RGB")
        camera = camera.resized(*photo.size)
        u, v, _ = camera.project(points)
        left = max(0, int(np.floor(u.min()))-2)
        top = max(0, int(np.floor(v.min()))-2)
        right = min(photo.width, int(np.ceil(u.max()))+3)
        bottom = min(photo.height, int(np.ceil(v.max()))+3)
        photo = photo.crop((left, top, right, bottom))
        uv = np.column_stack([(u-left+.5)/photo.width, (v-top+.5)/photo.height])
        builder.add_textured_mesh(geometry, np.arange(len(points)).reshape(-1, 3), uv,
                                  np.asarray(photo, dtype=np.uint8), f"{room}:{index}")
        used.append({"frame_id": camera.frame_id,
                     "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                     "crop_pixels": [left, top, right, bottom], "triangles": len(faces)})
    glb = output/"photo-mesh.glb"
    glb.write_bytes(builder.build())
    return glb, used


def selection_string(args) -> str:
    if args.partial:
        return (f"choose_views_partial (>=5/7 samples + spread, min_facing={args.min_facing}, "
                f"centre={'required' if not args.no_centre else 'not required'}, "
                f"depth_reference={'display' if args.display_depth else 'full geometry'})")
    return "choose_views (all corners and centre, full geometry depth)"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("room", choices=CAPTURES)
    parser.add_argument("--captures", type=Path, default=Path("datasets/phone/moffett"))
    parser.add_argument("--output", type=Path, default=Path("runs/moffett/photo-surfaces"))
    parser.add_argument("--faces", type=int, default=250000)
    parser.add_argument("--cameras", type=int, default=160)
    parser.add_argument("--visibility-width", type=int, default=1280)
    parser.add_argument("--partial", action="store_true",
                        help="allow partial face support (>=5 of 7 samples visible) instead of all corners")
    parser.add_argument("--min-facing", type=float, default=0.2,
                        help="facing threshold for partial selection (default 0.2)")
    parser.add_argument("--display-depth", action="store_true",
                        help="run occlusion checks on the display mesh instead of the full geometry")
    parser.add_argument("--no-centre", action="store_true",
                        help="partial rule without the centre-sample requirement (spread condition still applies)")
    parser.add_argument("--snap", action="store_true",
                        help="snap display vertices back onto the measured full-resolution surface")
    parser.add_argument("--exclude-frame", action="append", default=[], metavar="FRAME_ID",
                        help="exclude this captured frame from source selection; repeat to exclude several")
    parser.add_argument("--exclude-from", type=Path, default=None, metavar="FILE",
                        help="file of line-separated frame IDs to exclude from source selection")
    parser.add_argument("--split-manifest", type=Path, default=None, metavar="FILE",
                        help="frozen splits-v2.json; its validation and test frames are excluded and "
                             "the split identity is recorded in the bake manifest as provenance")
    parser.add_argument("--region-from", type=Path, default=None, metavar="FILE",
                        help="frozen region.json; keep a higher triangle budget inside its box during "
                             "decimation so measured walls are not erased from the display mesh")
    parser.add_argument("--region-face-fraction", type=float, default=0.6,
                        help="fraction of --faces reserved for the region (default 0.6)")
    parser.add_argument("--subdivide-region", action="store_true",
                        help="split region display faces into four midpoint subfaces before "
                             "snapping, so oblique photographed faces warp less per face")
    args = parser.parse_args()
    directory = args.captures/CAPTURES[args.room]
    output = args.output/args.room
    output.mkdir(parents=True, exist_ok=True)
    c2r = capture_to_room_from_payload(json.loads((directory/"room.json").read_text()))
    vertices, triangles = scan_geometry(directory/"lidar-mesh.json", c2r)
    mesh_sha = hashlib.sha256((directory/"lidar-mesh.json").read_bytes()).hexdigest()[:12]
    room_sha = hashlib.sha256((directory/"room.json").read_bytes()).hexdigest()[:12]
    region_faces, region_block = region_faces_of(vertices, triangles, args.region_from,
                                                 args.region_face_fraction)
    cache_tag = "-region" if args.region_from is not None else ""
    cache = output/f"display-{mesh_sha}-{room_sha}-{args.faces}-{args.region_face_fraction}{cache_tag}-{code_key()}.npz"
    excluded_frames, split_block = excluded_frames_from_args(args)
    display_vertices, display_triangles, display_region_mask = display_mesh(
        cache, vertices, triangles, args, region_faces)
    if args.subdivide_region and region_faces is not None:
        display_vertices, display_triangles = subdivide_masked_faces(
            display_vertices, display_triangles, display_region_mask)
        print(args.room, "display triangles after region subdivision:",
              len(display_triangles), flush=True)
    print(args.room, "display triangles", len(display_triangles), flush=True)
    snap_status = None
    if args.snap:
        display_vertices, snap_count, snap_max = snap_to_measured(display_vertices, vertices)
        snap_status = {"snapped_vertices": snap_count, "max_snap_distance_m": float(snap_max)}
        print(f"snapped {snap_count} display vertices; max distance {snap_max:.3f} m", flush=True)
    frames, cameras = source_cameras(directory, c2r, excluded_frames, args.cameras)
    small = [c.resized(args.visibility_width, round(c.height*args.visibility_width/c.width)) for c in cameras]

    def progress(current, count, fraction):
        if current % 10 == 0 or current == count:
            print(f"{args.room}: {current}/{count} views, photographed surface {fraction:.1%}", flush=True)

    depth_vertices = None if args.display_depth else vertices
    depth_triangles = None if args.display_depth else triangles
    if args.partial:
        assignment, areas = choose_views_partial(
            display_vertices, display_triangles, small, depth_vertices, depth_triangles, progress,
            min_facing=args.min_facing, centre_required=not args.no_centre)
    else:
        assignment, areas = choose_views(
            display_vertices, display_triangles, small, depth_vertices, depth_triangles, progress)
    np.savez_compressed(output/"view-assignment.npz", camera_index=assignment, face_area_m2=areas)
    glb, used = write_glb(output, args.room, args, cameras, frames, display_vertices,
                          display_triangles, assignment)
    manifest = {"room": args.room, "capture_id": CAPTURES[args.room], "capture_to_room": c2r.m,
                "vertex_snap": snap_status,
                "coordinate_system": "GLTF Y up: (scene X, scene Z, -scene Y)",
                "selection": selection_string(args),
                "camera_count": int(args.cameras),
                "excluded_frames": sorted(excluded_frames),
                "split": split_block,
                "region": region_block,
                "display_cache_key": cache.name,
                "code_identity": code_key(),
                "source_mesh_sha256": hashlib.sha256((directory/"lidar-mesh.json").read_bytes()).hexdigest(),
                "source_poses_sha256": hashlib.sha256((directory/"poses.json").read_bytes()).hexdigest(),
                "display_triangle_count": len(display_triangles), "display_only_decimation": True,
                "photographed_area_fraction": float(areas[assignment >= 0].sum()/areas.sum()),
                "unseen_surface": "neutral grey; no generated detail", "frames": used,
                "glb_bytes": glb.stat().st_size, "glb_sha256": hashlib.sha256(glb.read_bytes()).hexdigest()}
    (output/"photo-mesh.json").write_text(json.dumps(manifest, indent=2)+"\n")
    print(glb, glb.stat().st_size, "bytes", flush=True)


if __name__ == "__main__":
    main()
