"""Export preserved photos, calibrated ARKit cameras, and metric LiDAR seeds.

Nerfstudio cameras use OpenGL axes: the unchanged sensor image looks down -Z.
The exported world uses the existing scene's Z-up metre convention.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
from standardphysics_pipeline.ingest import capture_to_room_from_payload
from standardphysics_pipeline.textures.camera import load_cameras
from standardphysics_pipeline.textures.scan_colour import scan_geometry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prepared', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--voxel', type=float, default=.065)
    args = parser.parse_args()
    if args.voxel <= 0:
        parser.error('voxel size must be positive')
    frames = json.loads((args.prepared/'input-manifest.json').read_text())['frames']
    if not frames or len({f['capture_id'] for f in frames}) != 1:
        parser.error('one nonempty capture is required')
    if args.output.exists():
        parser.error('choose a new output directory to preserve dataset provenance')
    source = Path(frames[0]['source']).parent.parent
    room = capture_to_room_from_payload(json.loads((source/'room.json').read_text()))
    cameras = {c.frame_id: c for c in load_cameras(source/'poses.json', [f['frame_id'] for f in frames], room)}
    (args.output/'images').mkdir(parents=True)
    converted = []
    for f in frames:
        c = cameras[f['frame_id']]
        camera_to_world = np.linalg.inv(c.room_to_camera)@np.diag([1., -1., -1., 1.])
        relative = 'images/'+f['filename']
        os.link(Path(f['source']), args.output/relative)
        converted.append({'file_path': relative, 'transform_matrix': camera_to_world.tolist(),
                          'fl_x': c.fx, 'fl_y': c.fy, 'cx': c.cx, 'cy': c.cy,
                          'w': c.width, 'h': c.height})
    vertices, triangles = scan_geometry(source/'lidar-mesh.json', room)
    vertices = vertices[np.unique(triangles)]
    vertices = vertices[np.isfinite(vertices).all(axis=1)]
    _, indices = np.unique(np.floor(vertices/args.voxel).astype(np.int64), axis=0, return_index=True)
    points = vertices[indices]
    dtype = np.dtype([('x','<f4'),('y','<f4'),('z','<f4'),('red','u1'),('green','u1'),('blue','u1')])
    data = np.zeros(len(points), dtype=dtype)
    for i, key in enumerate(['x','y','z']):
        data[key] = points[:,i]
    for key in ['red','green','blue']:
        data[key] = 128
    header = ('ply\nformat binary_little_endian 1.0\ncomment LiDAR seed positions; neutral initial color\n'
              f'element vertex {len(points)}\nproperty float x\nproperty float y\nproperty float z\n'
              'property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n')
    (args.output/'lidar-seeds.ply').write_bytes(header.encode()+data.tobytes())
    (args.output/'transforms.json').write_text(json.dumps({'camera_model':'OPENCV','ply_file_path':'lidar-seeds.ply','frames':converted}))
    (args.output/'provenance.json').write_text(json.dumps({'frames': frames,'coordinate_system':'scene Z up, metres',
        'image_pixels':'unchanged original JPEGs','camera_poses':'captured ARKit calibration; no synthetic views',
        'seed_point_count':len(points),'seed_voxel_m':args.voxel,'seed_color':'neutral initialization, optimized from photographs',
        'source_manifest':str(args.prepared.resolve()),'measurement_note':'Trained splats are display geometry, not measured dimensions.'},indent=2)+'\n')
    print(f'{len(frames)} calibrated cameras; {len(points)} LiDAR seeds; {args.output}',flush=True)


if __name__ == '__main__':
    main()
