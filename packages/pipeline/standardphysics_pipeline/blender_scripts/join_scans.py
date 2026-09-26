"""Runs inside Blender. Sets several painted scans on one floor and writes them as one glTF.

    blender --background --python join_scans.py -- \
        --scan a.glb --placement "16 floats" --scan b.glb --placement "16 floats" --out floor.glb

Each scan arrives in its own room frame, as `colour_scan.py` wrote it. Its placement
is a row-major 4x4 in that frame, z up, moving it to where it stands on the floor.
The importer turns glTF's y up back into z up, so the placement applies as given.
"""

import argparse
import sys

import bpy
from mathutils import Matrix

MAX_TRIANGLES = 1_100_000
"""A whole floor of scans, thinned only past what a browser can still draw."""


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", action="append", required=True)
    parser.add_argument("--placement", action="append", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-triangles", type=int, default=MAX_TRIANGLES)
    args = parser.parse_args(argv)
    if len(args.scan) != len(args.placement):
        parser.error("every scan needs exactly one placement")
    return args


def placed(path: str, placement: str) -> list:
    """The scan's meshes, imported and moved to where the owner put them."""
    values = [float(value) for value in placement.split()]
    matrix = Matrix([values[0:4], values[4:8], values[8:12], values[12:16]])
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    meshes = [obj for obj in bpy.data.objects if obj not in before and obj.type == "MESH"]
    for obj in meshes:
        obj.matrix_world = matrix @ obj.matrix_world
    return meshes


def joined(meshes: list):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.ops.object.join()
    return bpy.context.view_layer.objects.active


def thin(obj, max_triangles: int) -> None:
    """Fewer faces for a floor of untextured scans; a photographed floor keeps every face.

    Each painted face has its own island in its atlas, and collapsing an edge
    between two faces blends two unrelated islands' coordinates, so a thinned
    photographed floor came out as smeared texture. The walks were each thinned
    to the viewer's budget when they were painted.
    """
    triangle_count = sum(len(polygon.vertices) - 2 for polygon in obj.data.polygons)
    if triangle_count <= max_triangles or len(obj.data.uv_layers):
        return
    modifier = obj.modifiers.new("thin", "DECIMATE")
    modifier.ratio = max_triangles / triangle_count
    bpy.ops.object.modifier_apply(modifier="thin")
    print(f"THINNED {triangle_count} -> {len(obj.data.polygons)}")


def main() -> None:
    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    meshes = [obj for path, placement in zip(args.scan, args.placement) for obj in placed(path, placement)]
    floor = joined(meshes)
    thin(floor, args.max_triangles)
    bpy.ops.export_scene.gltf(
        filepath=args.out, export_format="GLB", use_selection=False,
        export_yup=True, export_apply=True,
    )
    print("FLOOR_GLB_WRITTEN")


main()
