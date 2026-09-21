"""Verify candidate photo overlaps using triangle depths and metric rigid fits.

Only writes evidence. No scan revision is changed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from moffett_image_registration import CAPTURES, rigid_fit
from standardphysics_pipeline.ingest import capture_to_room_from_payload
from standardphysics_pipeline.textures.camera import load_cameras
from standardphysics_pipeline.textures.surface_photos import surface_depth


def features(name, index, output):
    cache = output/f'{name}-{index}-triangle-features.npz'
    if cache.exists():
        return dict(np.load(cache))
    root = Path('datasets/phone/moffett')/CAPTURES[name]
    pose = json.loads((root/'poses.json').read_text())[index]
    camera = load_cameras(root/'poses.json', [pose['frame_id']],
                         capture_to_room_from_payload(json.loads((root/'room.json').read_text())))[0]
    image = cv2.imread(str(root/'frames'/Path(pose['image']).name), cv2.IMREAD_GRAYSCALE)
    width = 1280
    height = round(image.shape[0]*width/image.shape[1])
    image = cv2.resize(image, (width, height))
    camera = camera.resized(width, height)
    kp, desc = cv2.SIFT_create(nfeatures=10000, contrastThreshold=.015).detectAndCompute(image, None)
    pixels = np.array([p.pt for p in kp])
    geometry = np.load('runs/moffett/'+name+'_geometry.npz')
    depth = surface_depth(camera, geometry['vertices'], geometry['triangles'])
    xy = np.rint(pixels).astype(int)
    z = depth[xy[:, 1], xy[:, 0]]
    local = np.column_stack(((pixels[:, 0]-camera.cx)/camera.fx,
                            (pixels[:, 1]-camera.cy)/camera.fy, np.ones(len(pixels))))*z[:, None]
    inverse = np.linalg.inv(camera.room_to_camera)
    with np.errstate(invalid='ignore'):
        world = local@inverse[:3, :3].T+inverse[:3, 3]
    result = dict(desc=desc, pixels=pixels, xyz=world, depth=z,
                  camera=camera.room_to_camera,
                  k=np.array([[camera.fx, 0, camera.cx], [0, camera.fy, camera.cy], [0, 0, 1.]]))
    np.savez_compressed(cache, **result)
    print(name, index, 'features', len(kp), 'depths', np.isfinite(z).sum(), flush=True)
    return result


def verify(a, b, ratio):
    matcher = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=100))
    matches = matcher.knnMatch(a['desc'], b['desc'], k=2)
    good = [m for m, n in matches if m.distance < ratio*n.distance]
    unique = {m.trainIdx: m for m in sorted(good, key=lambda m: -m.distance)}
    matches = list(unique.values())
    i = np.array([m.queryIdx for m in matches], dtype=int)
    j = np.array([m.trainIdx for m in matches], dtype=int)
    pa, pb = a['xyz'][i], b['xyz'][j]
    valid = np.isfinite(pa).all(1) & np.isfinite(pb).all(1)
    valid &= (a['depth'][i] < 15) & (b['depth'][j] < 15)
    pa, pb = pa[valid], pb[valid]
    if len(pa) < 6:
        return {'raw_matches': len(matches), 'lifted': len(pa)}
    rng = np.random.default_rng(42)
    selected = np.zeros(len(pa), dtype=bool)
    # Both scans are metric. Free-scale image-style fitting collapses unrelated
    # repeated locker handles onto a tiny cluster, so use rigid hypotheses only.
    for _ in range(5000):
        ids = rng.choice(len(pa), 2, replace=False)
        da, db = pa[ids[1], :2]-pa[ids[0], :2], pb[ids[1], :2]-pb[ids[0], :2]
        if np.linalg.norm(da) < .5 or abs(np.linalg.norm(da)-np.linalg.norm(db)) > .25:
            continue
        candidate = rigid_fit(pa[ids], pb[ids])
        residual = np.linalg.norm(pa@candidate[:3,:3].T+candidate[:3,3]-pb, axis=1)
        inliers = residual < .2
        if inliers.sum() > selected.sum():
            selected = inliers
    if selected.sum() < 2:
        return {'raw_matches': len(matches), 'lifted': len(pa), 'inliers':0}
    transform = rigid_fit(pa[selected], pb[selected])
    errors = np.linalg.norm(pa@transform[:3, :3].T+transform[:3, 3]-pb, axis=1)
    selected &= errors < .3
    if selected.sum() > 2:
        transform = rigid_fit(pa[selected], pb[selected])
    result = {'raw_matches': len(matches), 'lifted': len(pa), 'inliers': int(selected.sum()),
              'scale': 1.0, 'transform': transform.tolist(),
              'rmse_m': float(np.sqrt(np.mean(errors[selected]**2))) if selected.any() else None,
              'source_points': pa[selected].tolist(), 'target_points': pb[selected].tolist(),
              'source_pixels': a['pixels'][i][valid][selected].tolist(),
              'target_pixels': b['pixels'][j][valid][selected].tolist(),
              'image_width':1280}
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('pairs', nargs='+', help='bottom frame:left frame')
    p.add_argument('--output', type=Path, default=Path('runs/moffett/triangle-overlaps'))
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cv2.setNumThreads(2)
    for pair in args.pairs:
        first, second = map(int, pair.split(':'))
        a = features('bottom_left', first, args.output)
        b = features('left', second, args.output)
        for ratio in [.7, .8]:
            result = verify(a, b, ratio)
            result.update(source_frame=first, target_frame=second, ratio=ratio)
            (args.output/f'b{first}-l{second}-{ratio}.json').write_text(json.dumps(result, indent=2)+'\n')
            print({k:v for k,v in result.items() if k not in ['source_points','target_points','source_pixels','target_pixels']}, flush=True)


if __name__ == '__main__':
    main()
