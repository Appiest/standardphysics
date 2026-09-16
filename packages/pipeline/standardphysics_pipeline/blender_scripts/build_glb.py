"""Runs inside Blender. Builds display geometry from a SceneGraph and exports GLB.

    blender --background --python build_glb.py -- --graph g.json --out scene.glb

Geometry comes from the SceneGraph rather than from the scanned mesh, so the
thing the viewer draws is the thing the checks measured. A scanned USDZ is
prettier; it is also a separate surface that can disagree with the numbers.

glTF node names are arbitrary strings, so unlike USD these carry the node UUID
directly and the viewer can select by it with no mapping file.
"""

import argparse
import json
import sys
from math import cos, pi, radians

import bpy
from mathutils import Matrix, Vector

KIND_ORDER = ["floor", "wall", "window", "opening", "door", "object"]
MIN_DISPLAY_WALL_THICKNESS = 0.08
PORTAL_TOLERANCE = 0.12
PORTAL_ALIGNMENT = cos(radians(45))
MATERIALS: dict[str, object] = {}


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", required=True)
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def clear() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def material_for(node: dict):
    appearance = node.get("appearance") or {}
    return material_for_finish(
        appearance.get("base_color") or default_colour(node),
        appearance.get("material", "neutral"),
    )


def material_for_finish(colour: str, finish: str):
    key = f"finish:{colour.lower()}:{finish}"
    if key in MATERIALS:
        return MATERIALS[key]
    material = bpy.data.materials.new(key)
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    if principled:
        principled.inputs["Base Color"].default_value = hex_colour(colour)
        principled.inputs["Roughness"].default_value = {"metal": 0.28, "glass": 0.14, "wood": 0.52, "fabric": 0.9}.get(finish, 0.72)
        principled.inputs["Metallic"].default_value = 0.8 if finish == "metal" else 0.0
        if finish == "glass":
            principled.inputs["Transmission Weight"].default_value = 0.5
            if hasattr(material, "surface_render_method"):
                material.surface_render_method = "DITHERED"
    MATERIALS[key] = material
    return material


def default_colour(node: dict) -> str:
    if node["kind"] == "floor":
        return "#d6d0c4"
    if node["kind"] == "wall":
        return "#e7e1d5"
    categories = {"chair": "#a56f4c", "table": "#876343", "sofa": "#72818b", "storage": "#9c7b56", "counter": "#9c7b56"}
    return categories.get(node.get("raw_category"), "#aaa49a")


def hex_colour(value: str) -> tuple[float, float, float, float]:
    value = value.lstrip("#")
    if len(value) != 6:
        return (0.65, 0.64, 0.60, 1.0)
    return tuple(int(value[index:index + 2], 16) / 255 for index in range(0, 6, 2)) + (1.0,)


def node_matrix(node: dict) -> Matrix:
    values = node["transform"]["m"]
    return Matrix((values[0:4], values[4:8], values[8:12], values[12:16]))


def node_dimensions(node: dict) -> tuple[float, float, float]:
    dimensions = node["dimensions"]
    thickness = max(dimensions["y"], MIN_DISPLAY_WALL_THICKNESS) if node["kind"] == "wall" else dimensions["y"]
    return dimensions["x"], thickness, dimensions["z"]


def add_box(node: dict, center: tuple[float, float, float], size: tuple[float, float, float]):
    bpy.ops.mesh.primitive_cube_add(
        size=1.0
    )
    obj = bpy.context.active_object
    obj.matrix_world = node_matrix(node) @ Matrix.Translation(center) @ Matrix.Diagonal((*size, 1.0))
    obj.data.materials.append(material_for(node))
    return obj


def _part_values(node: dict, part: dict) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    dimensions = node_dimensions(node)
    center = tuple(part["center"][index] * dimensions[index] for index in range(3))
    size = tuple(part["size"][index] * dimensions[index] for index in range(3))
    return center, size


def _cylinder_rotation(axis: str) -> Matrix:
    if axis == "x":
        return Matrix.Rotation(pi / 2, 4, "Y")
    if axis == "y":
        return Matrix.Rotation(-pi / 2, 4, "X")
    return Matrix.Identity(4)


def _cylinder_scale(axis: str, size: tuple[float, float, float]) -> tuple[float, float, float]:
    if axis == "x":
        return size[2], size[1], size[0]
    if axis == "y":
        return size[0], size[2], size[1]
    return size


def _round_part(obj, part: dict, size: tuple[float, float, float]) -> None:
    width = min(size) * part.get("bevel", 0.0)
    if width <= 0:
        return
    bevel = obj.modifiers.new("soft edges", "BEVEL")
    bevel.width = width
    bevel.segments = 3
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=bevel.name)


def add_reconstruction_part(node: dict, part: dict):
    """A visual completion constrained to the node's measured local envelope."""
    center, size = _part_values(node, part)
    primitive = part["primitive"]
    if primitive == "box":
        bpy.ops.mesh.primitive_cube_add(size=1.0)
        transform = Matrix.Diagonal((*size, 1.0))
    elif primitive == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.5, depth=1.0)
        transform = _cylinder_rotation(part.get("axis", "z")) @ Matrix.Diagonal((*_cylinder_scale(part.get("axis", "z"), size), 1.0))
    else:
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=0.5)
        transform = Matrix.Diagonal((*size, 1.0))
    obj = bpy.context.active_object
    obj.matrix_world = node_matrix(node) @ Matrix.Translation(center) @ transform
    obj.data.materials.append(material_for_finish(part["base_color"], part["material"]))
    _round_part(obj, part, size)
    return obj


def join(parts: list, node: dict) -> None:
    if not parts:
        add_empty(node)
        return
    bpy.ops.object.select_all(action="DESELECT")
    for part in parts:
        part.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    obj = bpy.context.active_object
    obj.name = node["id"]
    obj.data.name = node["id"]
    obj["kind"] = node["kind"]
    obj["label"] = node["label"]
    obj["movable"] = node["movable"]


def add_empty(node: dict) -> None:
    """Keep a stable glTF node when a portal or fully-cut wall has no surface."""
    obj = bpy.data.objects.new(node["id"], None)
    bpy.context.collection.objects.link(obj)
    obj.matrix_world = node_matrix(node)
    obj["kind"] = node["kind"]
    obj["label"] = node["label"]
    obj["movable"] = node["movable"]


def wall_axes(wall: dict) -> tuple[int, int, float, float]:
    width, depth, _ = node_dimensions(wall)
    along, across = (0, 1) if width >= depth else (1, 0)
    return along, across, (width, depth)[along], (width, depth)[across]


def portal_half_extents(portal: dict, wall_inverse: Matrix, along: int, across: int) -> tuple[float, float, float]:
    """Project portal dimensions onto the wall's local axes.

    Portals may be rotated with their wall, so their span cannot be inferred
    from raw X/Y dimensions alone.
    """
    relation = wall_inverse.to_3x3() @ node_matrix(portal).to_3x3()
    size = portal["dimensions"]
    half = (size["x"] / 2, size["y"] / 2, size["z"] / 2)

    def projected(axis: int) -> float:
        return sum(abs(relation[axis][source]) * half[source] for source in range(3))

    return projected(along), projected(across), projected(2)


def portal_overlaps_wall(portal: dict, wall: dict, inverse: Matrix) -> bool:
    along, across, length, thickness = wall_axes(wall)
    local = inverse @ Vector((portal["transform"]["m"][3], portal["transform"]["m"][7], portal["transform"]["m"][11], 1.0))
    along_half, cross_half, vertical_half = portal_half_extents(portal, inverse, along, across)
    _, _, height = node_dimensions(wall)

    if portal.get("parent_id") != wall["id"]:
        portal_width, portal_depth, _ = node_dimensions(portal)
        portal_long_axis = 0 if portal_width >= portal_depth else 1
        portal_direction = (node_matrix(portal).to_3x3() @ Vector((1 if portal_long_axis == 0 else 0, 1 if portal_long_axis == 1 else 0, 0))).normalized()
        wall_direction = (node_matrix(wall).to_3x3() @ Vector((1 if along == 0 else 0, 1 if along == 1 else 0, 0))).normalized()
        if abs(portal_direction.dot(wall_direction)) < PORTAL_ALIGNMENT:
            return False
        if abs(local[across]) > thickness / 2 + cross_half + PORTAL_TOLERANCE:
            return False

    return (
        abs(local[along]) < length / 2 + along_half
        and abs(local[2]) < height / 2 + vertical_half
    )


def merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for low, high in sorted(intervals):
        if not merged or low > merged[-1][1]:
            merged.append((low, high))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
    return merged


def wall_parts(wall: dict, portals: list[dict]) -> list:
    width, depth, height = node_dimensions(wall)
    along, across, length, thickness = wall_axes(wall)
    inverse = node_matrix(wall).inverted()
    openings = []
    for portal in portals:
        if not portal_overlaps_wall(portal, wall, inverse):
            continue
        local = inverse @ Vector((portal["transform"]["m"][3], portal["transform"]["m"][7], portal["transform"]["m"][11], 1.0))
        along_half, _, vertical_half = portal_half_extents(portal, inverse, along, across)
        start, end = max(-length / 2, local[along] - along_half), min(length / 2, local[along] + along_half)
        lower, upper = max(-height / 2, local[2] - vertical_half), min(height / 2, local[2] + vertical_half)
        if end > start and upper > lower:
            openings.append((start, end, lower, upper))
    cuts = sorted({-length / 2, length / 2, *(edge for opening in openings for edge in opening[:2])})
    parts = []
    for start, end in zip(cuts, cuts[1:]):
        middle = (start + end) / 2
        vertical = merge_intervals([(lower, upper) for left, right, lower, upper in openings if left <= middle <= right])
        cursor = -height / 2
        for lower, upper in [*vertical, (height / 2, height / 2)]:
            low, high = cursor, lower
            if high > low:
                center = [0.0, 0.0, (low + high) / 2]
                size = [0.0, 0.0, high - low]
                center[along], size[along] = (start + end) / 2, end - start
                size[across] = thickness
                parts.append(add_box(wall, tuple(center), tuple(size)))
            cursor = max(cursor, upper)
    return parts


def furniture_parts(node: dict) -> list:
    reconstruction = node.get("reconstruction")
    if reconstruction and reconstruction.get("parts"):
        return [add_reconstruction_part(node, part) for part in reconstruction["parts"]]
    width, depth, height = node_dimensions(node)
    category = node.get("raw_category")
    floor = -height / 2
    if category == "table":
        top = min(0.12, height * 0.18)
        leg = min(0.08, width / 5, depth / 5)
        parts = [add_box(node, (0, 0, height / 2 - top / 2), (width, depth, top))]
        for x in (-width / 2 + leg / 2, width / 2 - leg / 2):
            for y in (-depth / 2 + leg / 2, depth / 2 - leg / 2):
                parts.append(add_box(node, (x, y, floor + (height - top) / 2), (leg, leg, height - top)))
        return parts
    if category == "chair":
        seat = min(0.12, height * 0.2)
        leg = min(0.06, width / 6, depth / 6)
        seat_height = floor + height * 0.48
        parts = [add_box(node, (0, 0, seat_height), (width, depth, seat))]
        for x in (-width / 2 + leg / 2, width / 2 - leg / 2):
            for y in (-depth / 2 + leg / 2, depth / 2 - leg / 2):
                parts.append(add_box(node, (x, y, floor + (seat_height - floor) / 2), (leg, leg, seat_height - floor)))
        parts.append(add_box(node, (0, depth / 2 - seat / 2, seat_height + (height / 2 - seat_height) / 2), (width, seat, height / 2 - seat_height)))
        return parts
    if category == "sofa":
        arm = min(0.18, width / 5)
        base = min(0.22, height * 0.35)
        return [
            add_box(node, (0, 0, floor + base / 2), (width, depth, base)),
            add_box(node, (0, depth / 2 - arm / 2, base / 2), (width, arm, height - base)),
            add_box(node, (-width / 2 + arm / 2, 0, floor + (height - base) / 2), (arm, depth, height - base)),
            add_box(node, (width / 2 - arm / 2, 0, floor + (height - base) / 2), (arm, depth, height - base)),
        ]
    if category in {"storage", "counter"}:
        body = min(0.12, height * 0.15)
        return [
            add_box(node, (0, 0, 0), (width, depth, height - body)),
            add_box(node, (0, 0, height / 2 - body / 2), (width, depth, body)),
        ]
    return [add_box(node, (0, 0, 0), (width, depth, height))]


def add_node(node: dict, portals: list[dict]) -> None:
    if node["kind"] == "wall":
        join(wall_parts(node, portals), node)
    elif node["kind"] == "object":
        join(furniture_parts(node), node)
    elif node["kind"] in {"door", "window", "opening"}:
        add_empty(node)
    else:
        join([add_box(node, (0, 0, 0), node_dimensions(node))], node)


def main() -> None:
    args = parse_args()
    graph = json.loads(open(args.graph).read())
    clear()

    nodes = sorted(
        graph["nodes"],
        key=lambda n: KIND_ORDER.index(n["kind"]) if n["kind"] in KIND_ORDER else 99,
    )
    portals = [node for node in nodes if node["kind"] in {"door", "window", "opening"}]
    for node in nodes:
        add_node(node, portals)

    bpy.ops.export_scene.gltf(
        filepath=args.out,
        export_format="GLB",
        export_apply=True,
        export_extras=True,
        export_yup=True,
    )
    print(f"GLB_WRITTEN {args.out} nodes={len(nodes)}")


if __name__ == "__main__":
    main()
