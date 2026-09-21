"""Reconstruct overlapping photo sections and fit them to recorded metric cameras.

Outputs remain review candidates; this never publishes a scene or edits captures.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import numpy as np
from standardphysics_pipeline.ingest import capture_to_room_from_payload
from standardphysics_pipeline.textures.camera import load_cameras


def similarity(source, target):
    a, b = source.mean(0), target.mean(0)
    x, y = source-a, target-b
    u, _, vt = np.linalg.svd(x.T@y)
    rotation = vt.T@u.T
    if np.linalg.det(rotation) < 0:
        vt[-1] *= -1
        rotation = vt.T@u.T
    scale = np.sum(y*(x@rotation.T))/np.sum(x*x)
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError('degenerate camera baseline')
    matrix = np.eye(4)
    matrix[:3, :3] = scale*rotation
    matrix[:3, 3] = b-scale*rotation@a
    return matrix


def metric_fit(directory, frames):
    manifest = {frame['filename']: frame for frame in frames}
    first = Path(frames[0]['source']).parent.parent
    transform = capture_to_room_from_payload(json.loads((first/'room.json').read_text()))
    cameras = {c.frame_id: c for c in load_cameras(first/'poses.json', [f['frame_id'] for f in frames], transform)}
    source, target, matched = [], [], []
    for sample in json.loads((directory/'recovered-poses.json').read_text())['samples']:
        frame = manifest.get(Path(sample['imagePath']).name)
        if frame is None:
            continue
        source.append(sample['translation'])
        target.append(cameras[frame['frame_id']].position)
        matched.append(frame['frame_id'])
    if len(source) < 8:
        raise ValueError(f'only {len(source)} registered cameras')
    source, target = np.array(source), np.array(target)
    if np.linalg.norm(np.ptp(target, axis=0)) < .5:
        raise ValueError('camera baseline under 0.5 m')
    matrix = similarity(source, target)
    for _ in range(5):
        errors = np.linalg.norm(source@matrix[:3, :3].T+matrix[:3, 3]-target, axis=1)
        keep = errors < max(.10, np.median(errors)*3)
        if keep.sum() < 8:
            raise ValueError('too few consistent camera poses')
        matrix = similarity(source[keep], target[keep])
    errors = np.linalg.norm(source@matrix[:3, :3].T+matrix[:3, 3]-target, axis=1)
    result = {'status': 'candidate_requires_visual_review', 'transform_to_room': matrix.tolist(),
              'room': frames[0]['room'], 'coordinate_system': 'scene Z up, metres',
              'camera_count': len(source), 'fit_camera_count': int(keep.sum()),
              'camera_rmse_m': float(np.sqrt(np.mean(errors[keep]**2))),
              'all_camera_rmse_m': float(np.sqrt(np.mean(errors**2))),
              'scale': float(np.cbrt(np.linalg.det(matrix[:3, :3]))),
              'frame_ids': matched, 'camera_errors_m': errors.tolist(),
              'measurement_note': 'Camera fit residual is not absolute dimensional accuracy.'}
    (directory/'metric-alignment.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


def _stage_inputs(directory: Path, images: Path, chunk: list, prepared: Path, start: int) -> None:
    """Put this chunk's photos where the reconstructor expects them.

    Hard links rather than copies, and an existing directory has to hold exactly
    the same inputs: a chunk that is half one run and half another reconstructs
    into something neither of them describes.
    """
    images.mkdir(parents=True, exist_ok=True)
    manifest_path = directory/'input-manifest.json'
    payload = {'frames': chunk, 'source_manifest': str(prepared.resolve()), 'start': start}
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != payload:
        raise ValueError(f'different existing input at {directory}')
    for frame in chunk:
        target = images/frame['filename']
        source = Path(frame['source'])
        if not target.exists():
            os.link(source, target)
        elif not target.samefile(source):
            raise ValueError(f'input does not match original {target}')
    manifest_path.write_text(json.dumps(payload, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prepared', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--cli', type=Path, default=Path('runs/moffett/reconstruct-capture'))
    parser.add_argument('--chunk-size', type=int, default=80)
    parser.add_argument('--overlap', type=int, default=24)
    parser.add_argument('--start-chunk', type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.overlap < args.chunk_size or args.chunk_size < 8 or args.start_chunk < 0:
        parser.error('require size >= 8, 0 <= overlap < size, and start >= 0')
    frames = json.loads((args.prepared/'input-manifest.json').read_text())['frames']
    if len({f['capture_id'] for f in frames}) != 1:
        parser.error('prepare one capture at a time')
    size = min(args.chunk_size, len(frames))
    starts = list(range(0, len(frames)-size+1, args.chunk_size-args.overlap))
    if not starts or starts[-1] != len(frames)-size:
        starts.append(len(frames)-size)
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for index, start in enumerate(starts):
        if index < args.start_chunk:
            continue
        directory = args.output/f'chunk-{index:03d}'
        images = directory/'images'
        chunk = frames[start:start+size]
        _stage_inputs(directory, images, chunk, args.prepared, start)
        command = [str(args.cli.resolve()), str(images.resolve()), str((directory/'model.usdz').resolve()),
                   '--detail', 'medium', '--ordering', 'unordered', '--checkpoint', str((directory/'checkpoints').resolve()),
                   '--poses-output', str((directory/'recovered-poses.json').resolve())]
        if (directory/'result.json').exists():
            records.append(json.loads((directory/'result.json').read_text()))
            continue
        print(f'chunk {index+1}/{len(starts)}: {size} photos starting at {start}', flush=True)
        with (directory/'process.log').open('w') as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
        result = {'chunk': index, 'start': start, 'exit_code': completed.returncode,
                  'model': str(directory/'model.usdz'), 'accepted_for_publication': False}
        if (directory/'recovered-poses.json').exists():
            try:
                result['metric_fit'] = metric_fit(directory, chunk)
            except (ValueError, OSError, KeyError) as error:
                result['metric_fit_error'] = str(error)
        records.append(result)
        (directory/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        (args.output/'batch-results.json').write_text(json.dumps(records, indent=2)+'\n')
        print(json.dumps({k: v for k, v in result.items() if k != 'metric_fit'}), flush=True)


if __name__ == '__main__':
    main()
