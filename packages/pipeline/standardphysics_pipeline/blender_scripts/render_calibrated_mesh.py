"""Blender entry point: verify every camera before rendering an existing mesh.

Run through scripts/render_calibrated_photo_mesh.py; this file needs bpy.
"""

import json
import sys
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector


def set_view(scene, obj, view):
    scene.render.resolution_x = view["width"]
    scene.render.resolution_y = view["height"]
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = view["pixel_aspect_x"]
    scene.render.pixel_aspect_y = view["pixel_aspect_y"]
    obj.matrix_world = Matrix(view["matrix_world"])
    cam = obj.data
    cam.type = "PERSP"
    cam.sensor_fit = "HORIZONTAL"
    cam.sensor_width = 36.0
    cam.lens = view["lens_mm"]
    cam.shift_x = view["shift_x"]
    cam.shift_y = view["shift_y"]
    cam.clip_start = 0.05
    cam.clip_end = 500.0
    bpy.context.view_layer.update()


def verify(scene, obj, view):
    set_view(scene, obj, view)
    rows = []
    # Check the actual renderer projection matrix too, not only a helper's math.
    projection = obj.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),
                                       x=view["width"], y=view["height"],
                                       scale_x=view["pixel_aspect_x"], scale_y=view["pixel_aspect_y"])
    for probe in view["verification_points"]:
        point = Vector(probe["room_point"])
        ndc = world_to_camera_view(scene, obj, point)
        helper_pixel = [ndc.x*view["width"]-.5, (1-ndc.y)*view["height"]-.5]
        clip = projection @ obj.matrix_world.inverted() @ Vector([*point, 1.0])
        matrix_pixel = [(clip.x/clip.w+1)*view["width"]/2-.5,
                        (1-clip.y/clip.w)*view["height"]/2-.5]
        error = max(abs(actual-wanted) for output in (helper_pixel, matrix_pixel)
                    for actual, wanted in zip(output, probe["pixel"]))
        if ndc.z <= 0 or error > 0.01:
            raise RuntimeError(f"camera {view['id']} failed corner/depth verification: {error}px")
        rows.append({"expected": probe["pixel"], "helper": helper_pixel,
                     "render_matrix": matrix_pixel, "max_error_px": error})
    return {"id": view["id"], "max_error_px": max(row["max_error_px"] for row in rows), "points": rows}


def diagnostic_materials():
    """Separate material-support passes; never infer support from RGB appearance."""
    masks = []
    for value in (0., 1.):
        material = bpy.data.materials.new(f"Diagnostic {value}")
        material.use_nodes = True
        tree = material.node_tree
        tree.nodes.clear()
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        emission = tree.nodes.new("ShaderNodeEmission")
        emission.inputs["Color"].default_value = (value, value, value, 1.)
        tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
        masks.append(material)
    originals = [(slot, slot.material) for obj in bpy.context.scene.objects if obj.type == "MESH"
                 for slot in obj.material_slots]
    support = [bool(material and material.use_nodes and any(node.type == "TEX_IMAGE"
               and node.image is not None for node in material.node_tree.nodes)) for _, material in originals]
    return masks, originals, support


def eevee_engine():
    """EEVEE under whichever name this Blender gives it.

    4.2 to 4.x call it BLENDER_EEVEE_NEXT, and both earlier and later versions,
    5.2.1 among them, call it BLENDER_EEVEE.
    """
    available = bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys()
    return "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in available else "BLENDER_EEVEE"


def main():
    plan_path = Path(sys.argv[sys.argv.index("--")+1])
    plan = json.loads(plan_path.read_text())
    output = plan_path.parent
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=plan["glb"])
    scene = bpy.context.scene
    scene.render.engine = eevee_engine()
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    scene.world = bpy.data.worlds.new("World")
    scene.world.color = (0.5, 0.48, 0.46)
    obj = bpy.data.objects.new("Calibrated view", bpy.data.cameras.new("Calibrated view"))
    bpy.context.collection.objects.link(obj)
    scene.camera = obj
    checks = [verify(scene, obj, view) for view in plan["views"]]
    (output/"camera-verification.json").write_text(json.dumps({"blender_version": bpy.app.version_string,
                                                             "views": checks}, indent=2)+"\n")
    masks, originals, support = diagnostic_materials()
    for view in plan["views"]:
        set_view(scene, obj, view)
        scene.world.color = (0.5, 0.48, 0.46)
        for slot, original in originals:
            slot.material = original
        scene.render.filepath = str(output/f"{view['id']}_candidate.png")
        bpy.ops.render.render(write_still=True)
        scene.world.color = (0, 0, 0)
        for name in ("geometry", "photo_support"):
            for (slot, _), photographed in zip(originals, support):
                slot.material = masks[int(name == "geometry" or photographed)]
            scene.render.filepath = str(output/f"{view['id']}_{name}.png")
            bpy.ops.render.render(write_still=True)
    print("CALIBRATED_MESH_RENDER_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
