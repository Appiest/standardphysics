"""Blender entry point: two cameras replay the exact saved scan campaign paths.

blender --background --python scan_video_blender.py -- --campaign report.json
    --graph scene_graph.json --mesh lidar-mesh --glb scene.glb --out frames
"""
import argparse
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

FPS = 18
SECONDS = 5
CAMERA_SIZE = (928, 730)


def material(name, color):
    value = bpy.data.materials.new(name)
    value.diffuse_color = (*color, 1)
    return value


def finish(obj, name, mat, parent=None):
    obj.name = name
    obj.data.materials.append(mat)
    if parent:
        obj.parent = parent
    for face in obj.data.polygons:
        face.use_smooth = obj.type == "MESH" and "cube" not in name
    return obj


def cube(name, location, scale, mat, parent=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.scale = scale
    return finish(obj, name, mat, parent)


def sphere(name, location, scale, mat, parent=None):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, location=location)
    obj = bpy.context.object
    obj.scale = scale
    return finish(obj, name, mat, parent)


def cylinder(name, location, radius, depth, mat, parent=None):
    bpy.ops.mesh.primitive_cylinder_add(vertices=20, radius=radius, depth=depth, location=location)
    return finish(bpy.context.object, name, mat, parent)


def ring(name, location, radius, tube, mat, parent=None, rotation=(0, 0, 0)):
    bpy.ops.mesh.primitive_torus_add(major_segments=32, minor_segments=8, location=location,
                                    major_radius=radius, minor_radius=tube, rotation=rotation)
    return finish(bpy.context.object, name, mat, parent)


def link(obj, start, end, radius):
    start, end = Vector(start), Vector(end)
    obj.location = (start+end)/2
    obj.rotation_euler = (end-start).to_track_quat("Z", "Y").to_euler()
    obj.scale = (radius, radius, (end-start).length/2)


def empty(name):
    obj = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(obj)
    return obj


def mesh_object(name, vertices, faces, mat):
    data = bpy.data.meshes.new(name)
    data.from_pydata(vertices.tolist(), [], faces.tolist())
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return obj


def capture_geometry(mesh, graph, glb):
    bpy.ops.import_scene.gltf(filepath=str(glb))
    for obj in list(bpy.context.scene.objects):
        obj.hide_render = True
        obj.hide_viewport = True
    vertices, faces = [], []
    offset = 0
    for part in mesh["parts"]:
        matrix = np.asarray(part["transform"]).reshape((4,4), order="F")
        points = np.asarray(part["vertices"]).reshape(-1,3) @ matrix[:3,:3].T + matrix[:3,3]
        points = np.column_stack((points[:,0], -points[:,2], points[:,1]-mesh["floorY"]))
        vertices.append(points)
        faces.append(np.asarray(part["triangles"]).reshape(-1,3)+offset)
        offset += len(points)
    vertices, faces = np.concatenate(vertices), np.concatenate(faces)
    mat = material("Captured LiDAR surface", (0.48, 0.55, 0.57))
    full = mesh_object("Actual captured LiDAR", vertices, faces, mat)
    cut_faces = faces[vertices[faces][:,:,2].max(axis=1) <= 1.45]
    cutaway = mesh_object("Camera cutaway only - collision geometry unchanged", vertices, cut_faces, mat)
    floor = next(node for node in graph["nodes"] if node["kind"] == "floor")
    dims, matrix = floor["dimensions"], np.asarray(floor["transform"]["m"]).reshape(4,4)
    corners = np.asarray([(x*dims['x']/2,y*dims['y']/2,z*dims['z']/2,1)
                          for x in (-1,1) for y in (-1,1) for z in (-1,1)]) @ matrix.T
    unique = np.unique(corners[:,:3],axis=0)
    center = unique[:,:2].mean(axis=0)
    ordered = unique[np.argsort(np.arctan2(unique[:,1]-center[1],unique[:,0]-center[0]))]
    ordered[:,2] = -0.025
    mesh_object("Measured floor support", ordered,
                np.asarray([[0,i,i+1] for i in range(1,len(ordered)-1)]),
                material("Floor", (0.23,0.28,0.30)))
    return full, cutaway


def actor():
    root = empty("Wheelchair avatar")
    skin = material("Skin", (0.70,0.45,0.31))
    sweater = material("Sweater", (0.18,0.52,0.45))
    pants = material("Trousers", (0.10,0.14,0.17))
    frame = material("Wheelchair frame", (0.48,0.55,0.58))
    rubber = material("Wheelchair wheels", (0.045,0.065,0.075))
    eyes = material("Eyes", (0.025,0.035,0.04))
    cube("seat cube", (0,0,0.51), (.49,.48,.07), pants, root)
    cube("backrest cube", (0,-.22,.73), (.47,.065,.43), pants, root)
    sphere("torso", (0,-.015,.79), (.21,.15,.28), sweater, root)
    head = sphere("head", (0,.005,1.13), (.125,.12,.15), skin, root)
    face = [head]
    for side in (-1,1):
        face.append(sphere("eye", (side*.043,.119,1.16), (.017,.012,.012), eyes, root))
        ring("wheel", (side*.34,-.05,.31), .285,.033,rubber,root,(0,math.pi/2,0))
        ring("hand rim", (side*.38,-.05,.31), .235,.012,frame,root,(0,math.pi/2,0))
        sphere("thigh", (side*.11,.16,.51), (.105,.27,.105), pants, root)
        sphere("shin", (side*.13,.38,.30), (.08,.075,.21), pants, root)
        cube("shoe cube", (side*.13,.45,.12), (.12,.23,.07), rubber, root)
        ring("front caster", (side*.24,.48,.095), .068,.022,rubber,root,(0,math.pi/2,0))
    arms = []
    for label in ("upper arm", "forearm"):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8)
        arms.append(finish(bpy.context.object,label,skin,root))
    sphere("left arm", (-.23,.12,.79), (.045,.08,.20), sweater,root)
    hand = sphere("right hand", (.24,.22,.65), (.045,.055,.035), skin,root)
    return root, face, arms, hand


def prop_for(run, graph):
    root = empty("Hypothetical "+run['task']['prop'])
    root.location = run['target']
    node = next(node for node in graph['nodes'] if node['id']==run['task']['target_node_id'])
    matrix = node['transform']['m']
    root.rotation_euler.z = math.atan2(matrix[4],matrix[0])
    accent = material("Hypothetical prop "+run['task']['id'], (0.96,0.60,0.24))
    white = material("Prop details "+run['task']['id'], (0.92,0.91,0.82))
    dark = material("Prop dark "+run['task']['id'], (0.07,0.11,0.15))
    kind = run['task']['prop']
    size = run['prop_size']
    if kind in {'medicine','drink'}:
        cylinder("hypothetical bottle", (0,0,.06),size[0]/2,.12,accent,root)
        cylinder("bottle cap", (0,0,.13),size[0]/2,.025,white,root)
    elif kind == 'computer':
        cube("laptop base cube", (0,0,0), (*size,.02),dark,root)
        cube("keyboard cube", (0,-.015,.014),(.26,.12,.008),white,root)
        screen = cube("laptop screen cube", (0,.10,.115),(.31,.016,.20),dark,root)
        screen.rotation_euler.x = -.15
        panel = cube("display cube", (0,.088,.12),(.28,.008,.17),accent,root)
        panel.rotation_euler.x = -.15
    else:
        thickness = {'food':.035,'bag':.15,'book':.025}.get(kind,.015)
        cube("hypothetical item cube",(0,0,thickness/2),(*size,thickness),accent,root)
    ring("Target marker",(0,0,-.018),max(size)*.70,.008,accent,root)
    return root


def path_line(run):
    curve = bpy.data.curves.new("Measured route", 'CURVE')
    curve.dimensions = '3D'
    curve.bevel_depth = .012
    curve.bevel_resolution = 1
    spline = curve.splines.new('POLY')
    spline.points.add(len(run['path'])-1)
    for point, saved in zip(spline.points,run['path']):
        point.co = (saved['x'],saved['y'],.04,1)
    obj = bpy.data.objects.new("Verified route "+run['task']['id'],curve)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material("Route "+run['task']['id'],(.25,.78,.66)))
    return obj


def camera(name):
    data = bpy.data.cameras.new(name)
    data.lens = 22
    data.clip_start = .025
    obj = bpy.data.objects.new(name,data)
    bpy.context.collection.objects.link(obj)
    return obj


def look_at(obj, point):
    obj.rotation_euler = (Vector(point)-obj.location).to_track_quat('-Z','Y').to_euler()


def path_position(run, fraction):
    points = np.asarray([[p['x'],p['y'],p['z']] for p in run['path']])
    lengths = np.concatenate(([0],np.cumsum(np.linalg.norm(np.diff(points,axis=0),axis=1))))
    distance = fraction*lengths[-1]
    index = min(len(points)-2,max(0,int(np.searchsorted(lengths,distance)-1)))
    if len(points)==1:
        return Vector(points[0]),Vector((0,1,0))
    blend = (distance-lengths[index])/max(1e-9,lengths[index+1]-lengths[index])
    position = points[index]*(1-blend)+points[index+1]*blend
    ahead = min(len(points)-1,index+10)
    forward = Vector(points[ahead]-position)
    return Vector(position),forward.normalized() if forward.length else Vector((0,1,0))


def pose(run, phase, rig, pov, follow):
    root, face, arms, hand = rig
    position, forward = path_position(run,min(1,phase/.68))
    target = Vector(run['target'])
    toward = Vector((target.x-position.x,target.y-position.y,0)).normalized()
    turn = min(1,max(0,(phase-.60)/.10))
    forward = (forward*(1-turn)+toward*turn).normalized()
    root.location = position
    root.rotation_euler.z = math.atan2(forward.y,forward.x)-math.pi/2
    bpy.context.view_layer.update()
    shoulder = Vector((.19,.035,run['profile']['shoulder_height']))
    # The hand illustration stops at the configured reach envelope.
    world_shoulder = position+Vector((0,0,run['profile']['shoulder_height']))
    world_reach = target-world_shoulder
    endpoint = world_shoulder+world_reach.normalized()*min(world_reach.length,run['profile']['arm_length'])
    local_endpoint = root.matrix_world.inverted() @ endpoint
    reach = min(1,max(0,(phase-.72)/.20))
    hand.location = Vector((.24,.22,.65)).lerp(local_endpoint,reach)
    elbow = (shoulder+hand.location)/2+Vector((.08,-.04,-.06))*(1-reach*.7)
    link(arms[0],shoulder,elbow,.043)
    link(arms[1],elbow,hand.location,.034)
    pov.location = position+Vector((forward.x*.13,forward.y*.13,run['profile']['eye_height']))
    gaze = pov.location+forward*1.0
    gaze.z -= .06
    gaze = gaze.lerp(target,min(1,max(0,(phase-.65)/.15)))
    look_at(pov,gaze)
    right = Vector((forward.y,-forward.x,0))
    follow.location = position+forward*1.8+right*1.8+Vector((0,0,2.1))
    look_at(follow,position+Vector((0,0,.72)))


def render(args):
    report = json.loads(args.campaign.read_text())
    graph = json.loads(args.graph.read_text())
    captured = json.loads(args.mesh.read_text())
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.filepaths.save_version = 0
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.render.resolution_x,scene.render.resolution_y = CAMERA_SIZE
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.film_transparent = False
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'MATERIAL'
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = 'BOTH'
    scene.display.shading.background_type = 'WORLD'
    scene.world = bpy.data.worlds.new("Backdrop")
    scene.world.color = (.025,.04,.05)
    scene.view_settings.view_transform = 'Standard'
    scene.render.fps = FPS
    full,cutaway = capture_geometry(captured,graph,args.glb)
    rig = actor()
    pov,follow = camera("Eye-level POV"),camera("Third-person follow")
    props = [prop_for(run,graph) for run in report['recorded_runs']]
    paths = [path_line(run) for run in report['recorded_runs']]
    frames = [int(value) for value in args.frames.split(',')] if args.frames else range(len(props)*FPS*SECONDS)
    args.out.mkdir(parents=True,exist_ok=True)
    for view,cam in (("pov",pov),("third-person",follow)):
        if args.view and view != args.view:
            continue
        directory = args.out/view
        directory.mkdir(exist_ok=True)
        scene.camera = cam
        full.hide_render = view != 'pov'
        cutaway.hide_render = view == 'pov'
        for obj in rig[1]:
            obj.hide_render = view == 'pov'
        for frame in frames:
            index,tick = divmod(frame,FPS*SECONDS)
            for prop_index,prop in enumerate(props):
                for child in prop.children_recursive:
                    child.hide_render = prop_index != index
                paths[prop_index].hide_render = prop_index != index
            pose(report['recorded_runs'][index],tick/(FPS*SECONDS-1),rig,pov,follow)
            scene.render.filepath = str(directory/f"{frame:05d}.png")
            bpy.ops.render.render(write_still=True)
        print(f"CAMERA_COMPLETE {view}",flush=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.out/'bread-test-replay.blend'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('campaign','graph','mesh','glb','out'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--frames',default='')
    parser.add_argument('--view',choices=['pov','third-person'])
    argv = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    render(parser.parse_args(argv))


if __name__ == '__main__':
    main()
