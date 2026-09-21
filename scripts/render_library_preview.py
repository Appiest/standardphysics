"""Render a high-resolution preview image of the unified Moffett Library 3D model."""

import sys

import bpy
import mathutils

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
glb_path = argv[0]
out_png = argv[1]

# Clear default scene
bpy.ops.wm.read_factory_settings(use_empty=True)

# Import GLB
bpy.ops.import_scene.gltf(filepath=glb_path)

# Add Camera
cam_data = bpy.data.cameras.new(name="PreviewCam")
cam_data.lens = 35
cam_obj = bpy.data.objects.new("PreviewCam", cam_data)
bpy.context.scene.collection.objects.link(cam_obj)
bpy.context.scene.camera = cam_obj

# Camera position: elevated overview of the library floor
cam_obj.location = (0, -28, 22)
look_at = mathutils.Vector((0, 0, 0))
direction = look_at - cam_obj.location
rot_quat = direction.to_track_quat("-Z", "Y")
cam_obj.rotation_euler = rot_quat.to_euler()

# Add lighting
light_data = bpy.data.lights.new(name="Sun", type="SUN")
light_data.energy = 2.5
light_obj = bpy.data.objects.new("Sun", light_data)
bpy.context.scene.collection.objects.link(light_obj)
light_obj.rotation_euler = (0.785, 0.35, 0.785)

# Render settings
bpy.context.scene.render.resolution_x = 1600
bpy.context.scene.render.resolution_y = 1000
bpy.context.scene.render.filepath = out_png
bpy.context.scene.render.image_settings.file_format = "PNG"

# Render
bpy.ops.render.render(write_still=True)
print("PREVIEW_RENDER_COMPLETE")
