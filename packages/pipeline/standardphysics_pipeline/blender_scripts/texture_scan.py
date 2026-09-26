"""Runs inside Blender. Lays a baked atlas over an unwrapped scan and writes the glTF the viewer reads.

    blender --background --python texture_scan.py -- --mesh unwrapped.npz --atlas atlas-0.jpg --atlas atlas-1.jpg --out scan.glb

Each triangle takes the atlas the unwrap packed it into, one material per atlas.

The atlas is an sRGB photograph of the surface, so the viewer shows the colours
the camera recorded rather than brightening them a second time.
"""

import argparse
import sys

import bpy
import numpy as np


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--mesh", required=True)
    parser.add_argument("--atlas", action="append", required=True)
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def build(vertices: np.ndarray, triangles: np.ndarray, uv: np.ndarray, atlases: np.ndarray):
    mesh = bpy.data.meshes.new("scan")
    mesh.from_pydata(vertices.tolist(), [], triangles.tolist())
    layer = mesh.uv_layers.new(name="UVMap")
    layer.data.foreach_set("uv", uv.astype(np.float32).ravel())
    mesh.polygons.foreach_set("material_index", atlases.astype(np.int32))
    obj = bpy.data.objects.new("scan", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def photographed(obj, atlas_path: str, name: str) -> None:
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    shader = tree.nodes.get("Principled BSDF")
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = bpy.data.images.load(atlas_path)
    tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    shader.inputs["Roughness"].default_value = 0.9
    obj.data.materials.append(material)


def main() -> None:
    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    archive = np.load(args.mesh)
    atlases = archive["atlases"] if "atlases" in archive else np.zeros(len(archive["triangles"]), dtype=np.int64)
    obj = build(archive["vertices"], archive["triangles"], archive["uv"], atlases)
    for index, atlas_path in enumerate(args.atlas):
        photographed(obj, atlas_path, f"scan-{index}")
    bpy.ops.export_scene.gltf(
        filepath=args.out, export_format="GLB", use_selection=False,
        export_yup=True, export_apply=True, export_image_format="AUTO",
    )
    print("SCAN_GLB_WRITTEN")


main()
