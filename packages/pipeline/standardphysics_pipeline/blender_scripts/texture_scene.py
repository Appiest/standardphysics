"""Runs inside Blender. Lays out photo texture atlases on the display geometry, or applies baked atlases.

    blender --background --python texture_scene.py -- layout --graph g.json --floors f.json \
        --density d.json --blend scene.blend --triangles triangles.npz --meta meta.json
    blender --background --python texture_scene.py -- apply --blend scene.blend \
        --atlases atlas-0.jpg atlas-1.jpg --out scene.glb

The geometry comes from build_glb node for node, so a textured mesh and the
plain mesh a check selects are the same shape under the same node id. Vertices
are stored relative to their node's transform, which makes the UVs and the
baked photos belong to the node wherever it is later placed.
"""

import argparse
import json
import math
import pathlib
import sys

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import build_glb as geometry  # noqa: E402

ATLAS_SIZE = 2048
MAX_ATLASES = 4
CHART_GUTTER = 8
MIN_DENSITY = 24.0
MAX_DENSITY = 1024.0
UNWRAP_ANGLE = math.radians(66)
DENSITY_SEARCH_STEPS = 24


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    modes = parser.add_subparsers(dest="mode", required=True)
    layout = modes.add_parser("layout")
    for name in ("graph", "floors", "density", "blend", "triangles", "meta"):
        layout.add_argument(f"--{name}", required=True)
    apply = modes.add_parser("apply")
    apply.add_argument("--blend", required=True)
    apply.add_argument("--atlases", nargs="+", required=True)
    apply.add_argument("--coverage", nargs="+", required=True)
    apply.add_argument("--out", required=True)
    points = modes.add_parser("points")
    points.add_argument("--mesh", required=True)
    points.add_argument("--out", required=True)
    return parser.parse_args(argv)


def add_floor(node: dict, polygon: list) -> None:
    inverse = geometry.node_matrix(node).inverted()
    local = [inverse @ Vector((x, y, 0.0, 1.0)) for x, y in polygon]
    mesh = bpy.data.meshes.new(node["id"])
    mesh.from_pydata([tuple(point[:3]) for point in local], [], [list(range(len(local)))])
    obj = bpy.data.objects.new(node["id"], mesh)
    bpy.context.collection.objects.link(obj)
    obj.matrix_world = geometry.node_matrix(node)
    obj.data.materials.append(geometry.material_for(node))
    for key in ("kind", "label", "movable"):
        obj[key] = node[key]


def build_scene(graph: dict, floors: dict) -> list[dict]:
    geometry.clear()
    nodes = sorted(graph["nodes"], key=lambda n: geometry.KIND_ORDER.index(n["kind"]) if n["kind"] in geometry.KIND_ORDER else 99)
    portals = [node for node in nodes if node["kind"] in {"door", "window", "opening"}]
    for node in nodes:
        if node["kind"] == "floor" and node["id"] in floors:
            add_floor(node, floors[node["id"]])
        else:
            geometry.add_node(node, portals)
    return nodes


def rest_on_node(obj, node: dict) -> None:
    node_matrix = geometry.node_matrix(node)
    obj.data.transform(node_matrix.inverted() @ obj.matrix_world)
    obj.matrix_world = node_matrix


def unwrap(obj) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=UNWRAP_ANGLE, island_margin=0.02, area_weight=0.0, correct_aspect=True, scale_to_bounds=False)
    bpy.ops.object.mode_set(mode="OBJECT")


def surface_and_uv_area(obj) -> tuple[float, float]:
    mesh = obj.data
    mesh.calc_loop_triangles()
    uv = mesh.uv_layers.active.data
    surface = sum(triangle.area for triangle in mesh.loop_triangles)
    used = sum(_uv_triangle_area([uv[index].uv for index in triangle.loops]) for triangle in mesh.loop_triangles)
    return surface, max(used, 1e-9)


def _uv_triangle_area(corners) -> float:
    (ax, ay), (bx, by), (cx, cy) = corners
    return abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) / 2


def chart_sides(charts: list[dict], density_cap: float) -> list[int]:
    usable = ATLAS_SIZE - 2 * CHART_GUTTER
    return [
        min(usable, max(8, math.ceil(math.sqrt(chart["surface"] / chart["uv_area"]) * min(chart["density"], density_cap))))
        for chart in charts
    ]


def shelf_pack(sides: list[int]) -> list[tuple[int, int, int]] | None:
    """Atlas, x and y for each square chart, tallest first, or None when four atlases are not enough."""
    placements: list[tuple[int, int, int] | None] = [None] * len(sides)
    atlas, cursor_x, shelf_y, shelf_height = 0, 0, 0, 0
    for index in sorted(range(len(sides)), key=lambda i: -sides[i]):
        cell = sides[index] + 2 * CHART_GUTTER
        if cursor_x + cell > ATLAS_SIZE:
            cursor_x, shelf_y, shelf_height = 0, shelf_y + shelf_height, 0
        if shelf_y + cell > ATLAS_SIZE:
            atlas, cursor_x, shelf_y, shelf_height = atlas + 1, 0, 0, 0
        if atlas >= MAX_ATLASES:
            return None
        placements[index] = (atlas, cursor_x + CHART_GUTTER, shelf_y + CHART_GUTTER)
        cursor_x += cell
        shelf_height = max(shelf_height, cell)
    return placements


def allocate(charts: list[dict]) -> tuple[list[int], list[tuple[int, int, int]]]:
    """The highest shared detail cap whose charts still fit, each chart kept at or below its photo detail."""
    low, high = MIN_DENSITY, MAX_DENSITY
    best = shelf_pack(chart_sides(charts, low))
    if best is None:
        raise RuntimeError("the room is too large to texture at the minimum detail")
    best_sides = chart_sides(charts, low)
    for _ in range(DENSITY_SEARCH_STEPS):
        middle = (low + high) / 2
        packed = shelf_pack(chart_sides(charts, middle))
        if packed is None:
            high = middle
            continue
        low, best, best_sides = middle, packed, chart_sides(charts, middle)
    return best_sides, best


def place_uvs(obj, side: int, placement: tuple[int, int, int]) -> None:
    atlas, x, y = placement
    for loop_uv in obj.data.uv_layers.active.data:
        loop_uv.uv = ((x + loop_uv.uv[0] * side) / ATLAS_SIZE, (y + loop_uv.uv[1] * side) / ATLAS_SIZE)
    obj["atlas"] = atlas


def base_colour(obj) -> list[float]:
    material = obj.data.materials[0] if obj.data.materials else None
    return material_colour(material)


def material_colour(material) -> list[float]:
    principled = material.node_tree.nodes.get("Principled BSDF") if material and material.use_nodes else None
    if principled is None:
        return [0.65, 0.64, 0.60]
    return list(principled.inputs["Base Color"].default_value)[:3]


def triangle_arrays(objects: list) -> dict[str, np.ndarray]:
    world, uvs, owners, colours = [], [], [], []
    for owner, obj in enumerate(objects):
        mesh = obj.data
        mesh.calc_loop_triangles()
        uv = mesh.uv_layers.active.data
        for triangle in mesh.loop_triangles:
            world.append([list(obj.matrix_world @ mesh.vertices[index].co) for index in triangle.vertices])
            uvs.append([list(uv[index].uv) for index in triangle.loops])
            owners.append(owner)
            material = obj.data.materials[triangle.material_index] if triangle.material_index < len(obj.data.materials) else None
            colours.append(material_colour(material))
    return {
        "world": np.asarray(world, dtype=np.float64).reshape(-1, 3, 3),
        "uv": np.asarray(uvs, dtype=np.float64).reshape(-1, 3, 2),
        "owner": np.asarray(owners, dtype=np.int32),
        "base_colour": np.asarray(colours, dtype=np.float32).reshape(-1, 3),
    }


def layout(args: argparse.Namespace) -> None:
    graph = json.loads(pathlib.Path(args.graph).read_text())
    floors = json.loads(pathlib.Path(args.floors).read_text())
    density = json.loads(pathlib.Path(args.density).read_text())
    nodes = {node["id"]: node for node in build_scene(graph, floors)}
    objects = sorted((obj for obj in bpy.data.objects if obj.type == "MESH" and obj.name in nodes), key=lambda obj: obj.name)
    charts = []
    for obj in objects:
        if nodes[obj.name]["kind"] != "floor":
            rest_on_node(obj, nodes[obj.name])
        unwrap(obj)
        surface, uv_area = surface_and_uv_area(obj)
        charts.append({"surface": surface, "uv_area": uv_area, "density": min(MAX_DENSITY, max(MIN_DENSITY, density.get(obj.name, MIN_DENSITY)))})
    sides, placements = allocate(charts)
    for obj, side, placement in zip(objects, sides, placements):
        place_uvs(obj, side, placement)
    np.savez(args.triangles, **triangle_arrays(objects))
    meta = {
        "atlas_size": ATLAS_SIZE,
        "atlas_count": 1 + max((placement[0] for placement in placements), default=0),
        "nodes": [
            {"id": obj.name, "atlas": int(obj["atlas"]), "surface": chart["surface"], "base_colour": base_colour(obj), "texels_per_meter": side * math.sqrt(chart["uv_area"] / chart["surface"])}
            for obj, chart, side in zip(objects, charts, sides)
        ],
    }
    pathlib.Path(args.meta).write_text(json.dumps(meta))
    bpy.ops.wm.save_as_mainfile(filepath=args.blend)
    print(f"TEXTURE_LAYOUT_WRITTEN atlases={meta['atlas_count']} nodes={len(objects)}")


def textured_material(material, image, cache: dict):
    key = (material.name, image.name)
    if key in cache:
        return cache[key]
    copy = material.copy()
    copy.name = f"{material.name}:photo:{image.name}"
    principled = copy.node_tree.nodes.get("Principled BSDF")
    texture = copy.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.interpolation = "Linear"
    copy.node_tree.links.new(texture.outputs["Color"], principled.inputs["Base Color"])
    cache[key] = copy
    return copy


def apply(args: argparse.Namespace) -> None:
    bpy.ops.wm.open_mainfile(filepath=args.blend)
    images = [bpy.data.images.load(str(pathlib.Path(path).resolve())) for path in args.atlases]
    coverage = [pathlib.Path(path).name for path in args.coverage]
    if len(images) != len(coverage):
        raise RuntimeError("each atlas needs one coverage mask")
    cache: dict = {}
    for obj in bpy.data.objects:
        if obj.type != "MESH" or "atlas" not in obj:
            continue
        image = images[int(obj["atlas"])]
        for slot in obj.material_slots:
            slot.material = textured_material(slot.material, image, cache)
        obj["texture_atlas"] = int(obj["atlas"])
        obj["coverage_mask"] = coverage[int(obj["atlas"])]
    bpy.context.scene["texture_coverage_masks"] = coverage
    bpy.ops.export_scene.gltf(
        filepath=args.out,
        export_format="GLB",
        export_apply=True,
        export_extras=True,
        export_yup=True,
        export_image_format="AUTO",
    )
    print(f"GLB_WRITTEN {args.out} atlases={len(images)}")


def points(args: argparse.Namespace) -> None:
    """Extract world-space triangle faces from a scan mesh.

    RoomPlan's scan is normally USDZ, while tests and repair tools often use
    OBJ or PLY. Blender's importers are the one place that can read all three.
    """
    geometry.clear()
    path = pathlib.Path(args.mesh)
    suffix = path.suffix.lower()
    if suffix in {".usd", ".usda", ".usdc", ".usdz"}:
        bpy.ops.wm.usd_import(filepath=str(path))
    elif suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path))
    elif suffix == ".ply":
        bpy.ops.wm.ply_import(filepath=str(path))
    else:
        raise RuntimeError(f"unsupported LiDAR mesh format: {suffix}")
    triangles = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        obj.data.calc_loop_triangles()
        for triangle in obj.data.loop_triangles:
            triangles.append([tuple(obj.matrix_world @ obj.data.vertices[index].co) for index in triangle.vertices])
    values = np.asarray(triangles, dtype=np.float32).reshape(-1, 3, 3)
    np.savez(args.out, triangles=values)
    print(f"LIDAR_TRIANGLES_WRITTEN triangles={len(values)}")


def main() -> None:
    args = parse_args()
    {"layout": layout, "apply": apply, "points": points}[args.mode](args)


if __name__ == "__main__":
    main()
