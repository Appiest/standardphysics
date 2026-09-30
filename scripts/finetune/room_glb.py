"""A 3D look at an ARKitScenes training room: the real scan next to the room the model trains on.

Writes two GLB files in one frame (the training room's: x and y as in the capture, z from its floor):

    scan.glb      the capture's mesh with its colours, merged onto a `SCAN_CELL` grid so a browser can show it
    training.glb  what the model sees: traced walls, the doorway, every annotated piece as its box, the route

and `index.html`, a three.js viewer with a switch for each.

    python scripts/finetune/room_glb.py --scan scan.ply --annotation annotation.json --window window.json --out dir
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import pathlib

import numpy as np
import trimesh
from arkit_shells import boxes, floor_height
from standardphysics_agents.training.windows import Window

SCAN_CELL = 0.03
CUTAWAY_METERS = 2.0
"""Scan faces entirely above this height are dropped, like a dollhouse with its ceiling off."""
COLOURS = {
    "wall": (60, 60, 66, 230), "door": (230, 120, 40, 255), "chair": (86, 132, 196, 170), "stool": (86, 132, 196, 170),
    "sofa": (86, 132, 196, 170), "bench": (86, 132, 196, 170), "table": (170, 120, 70, 170),
}
FIXTURE = (190, 160, 200, 150)
ROUTE = (220, 50, 50, 255)


def merged_scan(path: pathlib.Path, floor_z: float) -> trimesh.Trimesh:
    """The scan with vertices merged on a grid, keeping one colour per merged vertex."""
    mesh = trimesh.load(path, process=False)
    cells = np.round(mesh.vertices / SCAN_CELL).astype(np.int64)
    unique, first, inverse = np.unique(cells, axis=0, return_index=True, return_inverse=True)
    faces = inverse.reshape(-1)[mesh.faces]
    faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])]
    faces = np.unique(np.sort(faces, axis=1), axis=0)
    vertices = unique * SCAN_CELL - np.array([0.0, 0.0, floor_z])
    faces = faces[vertices[faces][:, :, 2].min(axis=1) < CUTAWAY_METERS]
    colours = mesh.visual.vertex_colors[first]
    return trimesh.Trimesh(vertices=vertices, faces=faces, vertex_colors=colours, process=False)


def _box(node, colour) -> trimesh.Trimesh:
    size = [max(node.dimensions.x, 0.02), max(node.dimensions.y, 0.02), max(node.dimensions.z, 0.02)]
    box = trimesh.creation.box(extents=size)
    box.apply_transform(np.array(node.transform.m).reshape(4, 4))
    box.visual.face_colors = colour
    return box


def _route(window: Window) -> list[trimesh.Trimesh]:
    points = [np.array([stop.position.x, stop.position.y, 0.05]) for stop in window.scenario.stops]
    pieces = []
    for start, end in itertools.pairwise(points):
        length = float(np.linalg.norm(end - start))
        if length < 0.01:
            continue
        leg = trimesh.creation.box(extents=[length, 0.06, 0.02])
        angle = math.atan2(end[1] - start[1], end[0] - start[0])
        leg.apply_transform(trimesh.transformations.rotation_matrix(angle, [0, 0, 1]))
        leg.apply_translation((start + end) / 2)
        leg.visual.face_colors = ROUTE
        pieces.append(leg)
    for point in points:
        marker = trimesh.creation.icosphere(radius=0.12)
        marker.apply_translation(point + [0, 0, 0.1])
        marker.visual.face_colors = ROUTE
        pieces.append(marker)
    return pieces


def training_room(window: Window) -> trimesh.Scene:
    scene = trimesh.Scene()
    for node in window.graph.nodes:
        if node.kind == "floor":
            continue
        colour = COLOURS.get(node.kind) or COLOURS.get(node.raw_category, FIXTURE)
        scene.add_geometry(_box(node, colour), node_name=f"{node.label}-{str(node.id)[:8]}")
    for index, piece in enumerate(_route(window)):
        scene.add_geometry(piece, node_name=f"route-{index}")
    return scene


VIEWER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{margin:0;font:14px system-ui;background:#f4f3f0}}#bar{{position:fixed;top:12px;left:12px;background:#fff;
padding:10px 14px;border-radius:10px;box-shadow:0 2px 12px #0002}}label{{margin-right:14px}}</style>
<script type="importmap">{{"imports":{{"three":"https://unpkg.com/three@0.160.0/build/three.module.js",
"three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}}}</script></head>
<body><div id="bar"><strong>{title}</strong><br>
<label><input type="checkbox" id="scan" checked> Real scan</label>
<label><input type="checkbox" id="training" checked> Training room</label></div>
<script type="module">
import * as THREE from "three";
import {{OrbitControls}} from "three/addons/controls/OrbitControls.js";
import {{GLTFLoader}} from "three/addons/loaders/GLTFLoader.js";
const renderer = new THREE.WebGLRenderer({{antialias:true}}); renderer.setSize(innerWidth, innerHeight);
renderer.setPixelRatio(devicePixelRatio); document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene(); scene.background = new THREE.Color(0xf4f3f0);
scene.add(new THREE.HemisphereLight(0xffffff, 0x888888, 2.2));
const camera = new THREE.PerspectiveCamera(50, innerWidth/innerHeight, 0.05, 200); camera.up.set(0,0,1);
const controls = new OrbitControls(camera, renderer.domElement);
const layers = {{}};
for (const name of ["scan","training"]) {{
  new GLTFLoader().load(name + ".glb", gltf => {{
    gltf.scene.traverse(o => {{ if (o.isMesh) {{ o.material.side = THREE.DoubleSide;
      o.material.metalness = 0; o.material.roughness = 1;
      if (name === "training") {{ o.material.transparent = true; o.material.depthWrite = false; }} }} }});
    layers[name] = gltf.scene; scene.add(gltf.scene);
    const box = new THREE.Box3().setFromObject(gltf.scene), c = box.getCenter(new THREE.Vector3());
    const span = box.getSize(new THREE.Vector3()).length();
    controls.target.copy(c); camera.position.set(c.x - span*0.35, c.y - span*0.55, c.z + span*0.6); controls.update();
  }});
  document.getElementById(name).onchange = e => {{ if (layers[name]) layers[name].visible = e.target.checked; }};
}}
addEventListener("resize", () => {{ camera.aspect = innerWidth/innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight); }});
(function loop(){{ requestAnimationFrame(loop); renderer.render(scene, camera); }})();
</script></body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", type=pathlib.Path, required=True)
    parser.add_argument("--annotation", type=pathlib.Path, required=True)
    parser.add_argument("--window", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    window = Window.from_dict(json.loads(args.window.read_text()))
    raw = trimesh.load(args.scan, process=False)
    floor_z = floor_height(np.asarray(raw.vertices), boxes(json.loads(args.annotation.read_text())))
    scan = merged_scan(args.scan, floor_z)
    args.out.mkdir(parents=True, exist_ok=True)
    scan.export(args.out / "scan.glb")
    training_room(window).export(args.out / "training.glb")
    title = f"ARKitScenes room {window.window_id.split(':')[0].removeprefix('arkit-')}"
    (args.out / "index.html").write_text(VIEWER.format(title=title))
    print(json.dumps({"scan_faces": len(scan.faces), "scan_mb": round((args.out / "scan.glb").stat().st_size / 1e6, 1),
                      "training_pieces": len(window.graph.nodes)}))


if __name__ == "__main__":
    main()
