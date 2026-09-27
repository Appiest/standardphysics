"""Full 3D Coloured LiDAR Render for Moffett Library.

Projects calibrated video walkthrough frames onto the LiDAR mesh for each of the
four Moffett Library rooms (center, top, bottom left, left), transforms them
into the unified library floor coordinate system using our verified spatial
alignment, welds them into one unified coloured 3D scan, bakes the GLB with
Blender, and registers the texture build in the SQLite database.
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
import shutil
import sqlite3
import time

import numpy as np
from standardphysics_contracts import SceneGraph, TextureBuild, TextureCoverage
from standardphysics_pipeline.ingest import capture_to_room_from_payload
from standardphysics_pipeline.textures.camera import load_cameras
from standardphysics_pipeline.textures.project import evenly_spread
from standardphysics_pipeline.textures.scan_colour import (
    ColouredScan,
    _photo,
    colour_the_scan,
    scan_geometry,
    unused_vertices_removed,
    write_scan_glb,
)

SCAN_ID = "f143082d-f529-494b-b80d-97729234e334"
DB_PATH = pathlib.Path("services/api/var/standardphysics.sqlite3")
VAR_SCANS_DIR = pathlib.Path("services/api/var/scans")

ROOMS = [
    {
        "name": "center",
        "dir": pathlib.Path("/tmp/sp-appdata/Captures/454B3661-D3F8-46E8-ADAA-3123E759AA64"),
        "placement": {"yaw": 0.0, "tx": 0.0, "ty": 0.0, "cx": -0.68595, "cy": 1.15545},
        "photos": 50,
    },
    {
        "name": "top",
        "dir": pathlib.Path("/tmp/sp-appdata/Captures/BBB88E0A-A2C3-4611-AFE1-71C43454E8C6"),
        "placement": {"yaw": 34.5, "tx": 16.966, "ty": -1.035, "cx": -8.845, "cy": 0.653},
        "photos": 60,
    },
    {
        "name": "bottom left",
        "dir": pathlib.Path("/tmp/sp-appdata/Captures/D9946491-26FD-4864-9673-C88180328109"),
        "placement": {"yaw": 10.2, "tx": 7.535, "ty": 3.094, "cx": 1.160, "cy": 10.696},
        "photos": 40,
    },
    {
        "name": "left",
        "dir": pathlib.Path("/tmp/sp-appdata/Captures/A92A8ED6-87A5-474D-8514-D2D0E6A1F00D"),
        "placement": {"yaw": 95.0, "tx": -22.903, "ty": -10.968, "cx": 8.423, "cy": -0.434},
        "photos": 50,
    },
]


def transform_vertices(
    vertices: np.ndarray,
    yaw_deg: float,
    cx: float,
    cy: float,
    tx: float,
    ty: float,
) -> np.ndarray:
    """Rigid planar rotation about (cx, cy) by yaw_deg, followed by translation (tx, ty)."""
    yaw_rad = math.radians(yaw_deg)
    cos_y = math.cos(yaw_rad)
    sin_y = math.sin(yaw_rad)

    px = vertices[:, 0] - cx
    py = vertices[:, 1] - cy

    rx = px * cos_y - py * sin_y
    ry = px * sin_y + py * cos_y

    x_new = cx + rx + tx
    y_new = cy + ry + ty
    z_new = vertices[:, 2]

    return np.column_stack([x_new, y_new, z_new])


def main():
    print(f"=== Rendering Full Library for Scan {SCAN_ID} ===")
    total_start = time.time()

    all_vertices = []
    all_triangles = []
    all_colours = []
    all_seen = []
    total_photos_used = 0
    vertex_offset = 0

    for room in ROOMS:
        name = room["name"]
        cdir = room["dir"]
        placement = room["placement"]
        max_photos = room["photos"]

        print(f"\n--- Processing room '{name}' from {cdir.name} ---")
        t0 = time.time()

        # Load capture-to-room transform
        rjson = json.loads((cdir / "room.json").read_text())
        c2r = capture_to_room_from_payload(rjson)

        # Load LiDAR mesh in room space
        mesh_path = cdir / "lidar-mesh.json"
        vertices, triangles = scan_geometry(mesh_path, c2r)
        print(f"  Raw mesh: {len(vertices):,} vertices, {len(triangles):,} triangles ({time.time() - t0:.2f}s)")

        # Load cameras and match available photo frames
        frames_dir = cdir / "frames"
        poses_path = cdir / "poses.json"
        poses = json.loads(poses_path.read_text())
        frame_paths = {
            pose["frame_id"]: frames_dir / pathlib.Path(pose["image"]).name
            for pose in poses
            if pose.get("frame_id")
        }

        cameras = [
            camera
            for camera in load_cameras(poses_path, frame_paths, c2r)
            if frame_paths.get(camera.frame_id, pathlib.Path()).is_file()
        ]
        cameras = evenly_spread(cameras, max_photos)
        print(f"  Selected {len(cameras)} cameras from {len(poses)} available poses")

        t_img = time.time()
        resized = [camera.resized(*_photo(frame_paths[camera.frame_id]).shape[1::-1]) for camera in cameras]
        images = [_photo(frame_paths[camera.frame_id]) for camera in cameras]
        print(f"  Loaded {len(images)} photos in {time.time() - t_img:.2f}s")

        t_col = time.time()
        scan = unused_vertices_removed(colour_the_scan(vertices, triangles, resized, images))
        print(
            f"  Coloured {len(scan.vertices):,} vertices ({scan.painted_fraction:.1%} seen) in {time.time() - t_col:.2f}s"
        )
        total_photos_used += len(cameras)

        # Apply rigid spatial alignment to global library floor
        aligned_v = transform_vertices(
            scan.vertices,
            placement["yaw"],
            placement["cx"],
            placement["cy"],
            placement["tx"],
            placement["ty"],
        )

        all_vertices.append(aligned_v)
        all_triangles.append(scan.triangles + vertex_offset)
        all_colours.append(scan.colours)
        all_seen.append(scan.seen)

        vertex_offset += len(aligned_v)

    # Combine into one unified library floor mesh
    print("\n--- Merging all 4 rooms into unified library floor ---")
    unified_vertices = np.vstack(all_vertices).astype(np.float32)
    unified_triangles = np.vstack(all_triangles).astype(np.int32)
    unified_colours = np.vstack(all_colours).astype(np.float32)
    unified_seen = np.concatenate(all_seen)

    unified_scan = ColouredScan(
        vertices=unified_vertices,
        triangles=unified_triangles,
        colours=unified_colours,
        seen=unified_seen,
    )
    print(f"Unified model: {len(unified_vertices):,} vertices, {len(unified_triangles):,} triangles")
    print(f"Overall painted fraction: {unified_scan.painted_fraction:.1%}")

    # Export scan.glb with Blender
    build_key = hashlib.sha256(f"moffett-library-full-v1-{len(unified_vertices)}".encode()).hexdigest()
    output_dir = VAR_SCANS_DIR / SCAN_ID / "textures" / build_key
    output_dir.mkdir(parents=True, exist_ok=True)
    scan_glb_path = output_dir / "scan.glb"

    print(f"\n--- Baking GLB to {scan_glb_path} (max 800k triangles) ---")
    t_bake = time.time()
    write_scan_glb(unified_scan, scan_glb_path, max_triangles=800_000)
    print(f"Baked scan.glb ({scan_glb_path.stat().st_size / (1024*1024):.2f} MB) in {time.time() - t_bake:.2f}s")

    # Ensure clean model scene.glb is also present in this texture directory
    rev1_glb = VAR_SCANS_DIR / SCAN_ID / "revisions" / "1" / "scene.glb"
    scene_glb_path = output_dir / "scene.glb"
    if rev1_glb.exists():
        shutil.copyfile(rev1_glb, scene_glb_path)
        print(f"Copied clean model scene.glb to {scene_glb_path}")

    # Query revision 1 scene graph from SQLite
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT graph_json FROM revisions WHERE scan_id=? AND revision=1", (SCAN_ID,)
    ).fetchone()
    if not row:
        raise RuntimeError("Revision 1 not found in database")
    bake_graph = SceneGraph.model_validate_json(row["graph_json"])

    total_seconds = time.time() - total_start
    prefix = f"/api/scans/{SCAN_ID}/textures/{build_key}"

    result = TextureBuild(
        build_id=build_key,
        glb_url=f"{prefix}/scene.glb",
        scan_glb_url=f"{prefix}/scan.glb",
        coverage_mask_urls=[],
        bake_graph=bake_graph,
        coverage=TextureCoverage(
            textured_fraction=float(unified_scan.painted_fraction),
            nodes=[],
            needs_another_view=[],
        ),
        frames_used=total_photos_used,
        seconds=total_seconds,
    )

    (output_dir / "result.json").write_text(result.model_dump_json())

    # Update database
    with conn:
        conn.execute(
            "INSERT INTO texture_builds (scan_id, build_key, graph_json, inputs_json, result_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, datetime('now'))"
            " ON CONFLICT(scan_id, build_key) DO UPDATE SET result_json=excluded.result_json",
            (
                SCAN_ID,
                build_key,
                bake_graph.model_dump_json(),
                json.dumps({"poses": "poses", "frames": {}, "lidar": "lidar-mesh"}),
                result.model_dump_json(),
            ),
        )
    conn.close()

    print(f"\nSUCCESS! Build key: {build_key}")
    print(f"scan_glb_url: {result.scan_glb_url}")
    print(f"Total time elapsed: {total_seconds:.2f}s")


if __name__ == "__main__":
    main()
