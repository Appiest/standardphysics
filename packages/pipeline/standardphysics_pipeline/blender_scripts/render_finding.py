"""Runs inside Blender. Renders one finding as a still for the printed report.

    blender --background --python render_finding.py -- \
        --graph g.json --locus l.json --out finding.png

The screen version of a finding animates: camera flies in, everything else
fades, the measurement draws. Paper gets one frame, so this has to carry the
same information at once — the objects responsible picked out from the rest,
the measurement drawn across the gap it describes, and a viewpoint that makes
the problem legible rather than pretty.
"""

import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_glb import add_box, join, node_dimensions, wall_parts  # noqa: E402

SUBJECT = (0.85, 0.25, 0.21, 1.0)
NEUTRAL = (0.82, 0.80, 0.78, 1.0)
FLOOR = (0.92, 0.91, 0.89, 1.0)
INK = (0.11, 0.10, 0.10, 1.0)

PORTAL_KINDS = {"door", "window", "opening"}
LINE_RADIUS = 0.012
LABEL_HEIGHT = 0.55
LABEL_STANDOFF = 1.1
"""How far toward the camera the number stands, clear of the thing it measures."""


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", required=True)
    parser.add_argument("--locus", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--height", type=int, default=800)
    return parser.parse_args(argv)


def material(name, colour):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = colour
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 0.75
    return mat


def rotation_z(matrix):
    return math.atan2(matrix[4], matrix[0])


def add_node(node, subject_ids, materials, portals):
    """One node as it should look, with doorways taken out of the walls they sit in.

    A wall drawn as a solid slab hides the very thing a door finding is about:
    the camera is put somewhere a person could stand, and from there the shot
    is a blank wall. `wall_parts` cuts the opening out, the same way the
    viewer's geometry is built, so the doorway is a hole you can see through.
    """
    if node["kind"] in PORTAL_KINDS:
        return None
    obj = _shape_of(node, portals)
    if obj is None:
        return None
    obj.name = node["id"]
    obj.data.materials.append(materials[_material_name(node, subject_ids)])
    return obj


def _shape_of(node, portals):
    if node["kind"] == "wall":
        parts = wall_parts(node, portals)
        if not parts:
            return None
        join(parts, node)
        return bpy.context.active_object
    join([add_box(node, (0, 0, 0), node_dimensions(node))], node)
    return bpy.context.active_object


def _material_name(node, subject_ids):
    if node["id"] in subject_ids:
        return "subject"
    return "floor" if node["kind"] == "floor" else "neutral"


def draw_line(points, materials):
    """A dimension line as a thin bar with end caps, so it reads on paper."""
    if len(points) < 2:
        return
    for start, end in zip(points, points[1:]):
        a = Vector((start["x"], start["y"], start["z"]))
        b = Vector((end["x"], end["y"], end["z"]))
        span = b - a
        if span.length < 1e-6:
            continue
        bpy.ops.mesh.primitive_cylinder_add(
            radius=LINE_RADIUS, depth=span.length, location=(a + b) / 2
        )
        bar = bpy.context.active_object
        bar.rotation_mode = "QUATERNION"
        bar.rotation_quaternion = span.to_track_quat("Z", "Y")
        bar.data.materials.append(materials["ink"])

    for end in (points[0], points[-1]):
        bpy.ops.mesh.primitive_cylinder_add(
            radius=LINE_RADIUS * 2.2,
            depth=0.09,
            location=(end["x"], end["y"], end["z"]),
        )
        bpy.context.active_object.data.materials.append(materials["ink"])


def add_label(text, at, camera_position, materials):
    """The number, stood in front of whatever it measures.

    A measurement across a doorway sits in the plane of the wall, and a label
    left there is cut in half by the jambs on either side: "31.8 in" renders as
    ".8 in". Stepping it toward the camera puts the whole number in clear air.
    """
    toward = Vector((camera_position[0] - at["x"], camera_position[1] - at["y"], 0.0))
    if toward.length > 1e-6:
        toward = toward.normalized() * LABEL_STANDOFF
    bpy.ops.object.text_add(location=(
        at["x"] + toward.x, at["y"] + toward.y, at["z"] + LABEL_HEIGHT,
    ))
    label = bpy.context.active_object
    label.data.body = text
    label.data.size = 0.30
    label.data.align_x = "CENTER"
    label.data.align_y = "CENTER"
    label.data.materials.append(materials["ink"])

    facing = Vector(camera_position) - Vector((at["x"], at["y"], at["z"]))
    label.rotation_euler = facing.to_track_quat("Z", "Y").to_euler()

    # The label floats above the floor, so the sun drops a second copy of the
    # text underneath it that reads as a duplicate rather than as a shadow.
    label.visible_shadow = False


def place_camera(locus):
    position = locus["camera"]["position"]
    target = locus["camera"]["target"]
    bpy.ops.object.camera_add(location=(position["x"], position["y"], position["z"]))
    camera = bpy.context.active_object
    camera.data.lens_unit = "FOV"
    camera.data.angle = math.radians(locus["camera"]["fov_degrees"])

    direction = Vector((target["x"], target["y"], target["z"])) - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = camera


def light_scene():
    bpy.ops.object.light_add(type="SUN", location=(4, -6, 9))
    bpy.context.active_object.data.energy = 3.2
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.95, 0.95, 0.96, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 1.1
    bpy.context.scene.world = world


def pick_engine():
    available = bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys()
    for candidate in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        if candidate in available:
            return candidate
    return list(available)[0]


def main():
    args = parse_args()
    graph = json.loads(open(args.graph).read())
    locus = json.loads(open(args.locus).read())

    bpy.ops.wm.read_factory_settings(use_empty=True)
    materials = {
        "subject": material("subject", SUBJECT),
        "neutral": material("neutral", NEUTRAL),
        "floor": material("floor", FLOOR),
        "ink": material("ink", INK),
    }

    subject_ids = set(locus.get("node_ids") or [])
    portals = [node for node in graph["nodes"] if node["kind"] in PORTAL_KINDS]
    for node in graph["nodes"]:
        add_node(node, subject_ids, materials, portals)

    annotation = locus["annotation"]
    draw_line(annotation["points"], materials)
    place_camera(locus)
    add_label(
        annotation["label"],
        locus["point"],
        (
            locus["camera"]["position"]["x"],
            locus["camera"]["position"]["y"],
            locus["camera"]["position"]["z"],
        ),
        materials,
    )
    light_scene()

    scene = bpy.context.scene
    scene.render.engine = pick_engine()
    scene.render.resolution_x = args.width
    scene.render.resolution_y = args.height
    scene.render.filepath = args.out
    scene.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)
    print("RENDER_WRITTEN %s engine=%s" % (args.out, scene.render.engine))


if __name__ == "__main__":
    main()
