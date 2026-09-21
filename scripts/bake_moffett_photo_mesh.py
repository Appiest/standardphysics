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
import trimesh
from moffett_image_registration import CAPTURES
from PIL import Image
from standardphysics_pipeline.ingest import capture_to_room_from_payload
from standardphysics_pipeline.textures.camera import load_cameras
from standardphysics_pipeline.textures.scan_colour import scan_geometry
from standardphysics_pipeline.textures.surface_photos import choose_views


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("room", choices=CAPTURES)
    parser.add_argument("--captures", type=Path, default=Path("datasets/phone/moffett"))
    parser.add_argument("--output", type=Path, default=Path("runs/moffett/photo-surfaces"))
    parser.add_argument("--faces", type=int, default=250000)
    parser.add_argument("--cameras", type=int, default=160)
    parser.add_argument("--visibility-width", type=int, default=1280)
    args = parser.parse_args()
    directory = args.captures/CAPTURES[args.room]
    output = args.output/args.room
    output.mkdir(parents=True, exist_ok=True)
    c2r = capture_to_room_from_payload(json.loads((directory/"room.json").read_text()))
    vertices, triangles = scan_geometry(directory/"lidar-mesh.json", c2r)
    cache = output/f"display-{args.faces}.npz"
    if cache.exists():
        saved = np.load(cache)
        display_vertices, display_triangles = saved["vertices"], saved["triangles"]
    else:
        mesh = trimesh.Trimesh(vertices, triangles, process=False)
        reduced = mesh.simplify_quadric_decimation(face_count=args.faces)
        display_vertices, display_triangles = reduced.vertices, reduced.faces
        np.savez_compressed(cache, vertices=display_vertices, triangles=display_triangles)
    print(args.room, "display triangles", len(display_triangles), flush=True)
    poses = json.loads((directory/"poses.json").read_text())
    frames = {p["frame_id"]: directory/"frames"/Path(p["image"]).name for p in poses}
    cameras = load_cameras(directory/"poses.json", frames, c2r)
    cameras = [cameras[i] for i in np.linspace(0, len(cameras)-1, min(args.cameras, len(cameras)), dtype=int)]
    small = [c.resized(args.visibility_width, round(c.height*args.visibility_width/c.width)) for c in cameras]

    def progress(current, count, fraction):
        if current % 10 == 0 or current == count:
            print(f"{args.room}: {current}/{count} views, photographed surface {fraction:.1%}", flush=True)

    assignment, areas = choose_views(display_vertices, display_triangles, small, vertices, triangles, progress)
    np.savez_compressed(output/"view-assignment.npz", camera_index=assignment, face_area_m2=areas)
    scene = trimesh.Scene()
    used = []
    for index in np.unique(assignment):
        faces = display_triangles[assignment == index]
        points = display_vertices[faces.ravel()]
        geometry = np.column_stack([points[:, 0], points[:, 2], -points[:, 1]])
        mesh = trimesh.Trimesh(geometry, np.arange(len(points)).reshape(-1, 3), process=False)
        if index < 0:
            mesh.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
                name="Unphotographed surface", baseColorFactor=[158, 153, 148, 255], metallicFactor=0, roughnessFactor=1,
                doubleSided=True))
        else:
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
            photo.format = "JPEG"
            uv = np.column_stack([(u-left+.5)/photo.width, 1-(v-top+.5)/photo.height])
            material = trimesh.visual.material.PBRMaterial(name=camera.frame_id, baseColorTexture=photo,
                metallicFactor=0, roughnessFactor=1, doubleSided=True)
            mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
            used.append({"frame_id": camera.frame_id, "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                         "crop_pixels": [left, top, right, bottom], "triangles": len(faces)})
        scene.add_geometry(mesh, node_name=f"{args.room}:{index}")

    def photographed_light(tree):
        # Recorded images already contain the room's lighting.
        tree.setdefault("extensionsUsed", []).append("KHR_materials_unlit")
        for material in tree["materials"]:
            material.setdefault("extensions", {})["KHR_materials_unlit"] = {}

    glb = output/"photo-mesh.glb"
    glb.write_bytes(trimesh.exchange.gltf.export_glb(scene, tree_postprocessor=photographed_light))
    manifest = {"room": args.room, "capture_id": CAPTURES[args.room], "capture_to_room": c2r.m,
                "coordinate_system": "GLTF Y up: (scene X, scene Z, -scene Y)",
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
