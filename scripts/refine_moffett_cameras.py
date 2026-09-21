#!/usr/bin/env python3
"""CPU-only, fixed-intrinsics COLMAP refinement for the Moffett center capture."""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pycolmap

ROOT=Path('runs/moffett'); DATA=ROOT/'splat-datasets/center'; PREP=ROOT/'photogrammetry-center-400'
WORK=ROOT/'camera-refinement/center400'; DB=WORK/'database.db'; OUT=ROOT/'splat-datasets/center-refined'
D=np.diag([1.,-1.,-1.,1.]); FIELDS=('capture_id','frame_id','pose_index','sha256')
def metric(r):
 return {'registered_images':r.num_reg_images(),'points3D':r.num_points3D(),'observations':r.compute_num_observations(),'mean_track_length':r.compute_mean_track_length(),'mean_reprojection_error_px':r.compute_mean_reprojection_error()}
def ang(a): return float(np.degrees(np.arccos(np.clip((np.trace(a)-1)/2,-1,1))))
def pose(t): return pycolmap.Rigid3d((D@np.linalg.inv(np.asarray(t['transform_matrix'],float)))[:3,:4])
def c2w(p):
 x=np.linalg.inv(D@np.vstack([p.matrix(),[0,0,0,1]])); return x.tolist()
def link(src,dst):
 dst.parent.mkdir(parents=True,exist_ok=True); os.link(src,dst)
def inputs():
 '''The frames, checked against each other before anything is reconstructed.

 Every one of these is a way the three files can disagree, and a reconstruction
 built on a mismatch is wrong without ever failing.
 '''
 transforms=json.loads((DATA/'transforms.json').read_text()); frames=transforms['frames']; manifest=json.loads((PREP/'input-manifest.json').read_text())['frames']; provenance=json.loads((DATA/'provenance.json').read_text())['frames']
 if not (len(frames) == len(manifest) == len(provenance) == 400): raise RuntimeError('expected 400 center frames')
 names=[f['filename'] for f in manifest]
 if any(any(a[k]!=b[k] for k in FIELDS) for a,b in zip(manifest,provenance,strict=True)): raise RuntimeError('source identity mismatch between manifest and provenance')
 if [x['file_path'].split('/')[-1] for x in frames]!=names: raise RuntimeError('transform ordering mismatch')
 images=DATA/'images'; masks=DATA/'masks'
 if any(not (images/n).is_file() or not (masks/(Path(n).stem+'.png')).is_file() for n in names): raise RuntimeError('missing image or person mask')
 return names,dict(zip(names,frames,strict=True)),images,masks,transforms,frames


def extract_and_match(names,by_name,images,masks):
 '''Find features in every photo and match them, with people masked out.

 The cameras are overwritten with the intrinsics the capture already recorded,
 so the reconstruction refines poses against known optics rather than solving
 for lenses it was told about.
 '''
 if WORK.exists(): shutil.rmtree(WORK)
 WORK.mkdir(parents=True); maskdir=WORK/'masks'; maskdir.mkdir()
 for n in names: os.symlink((masks/(Path(n).stem+'.png')).resolve(),maskdir/(n+'.png'))
 extract=pycolmap.FeatureExtractionOptions(); extract.use_gpu=False;extract.num_threads=4;extract.max_image_size=1024;extract.sift.max_num_features=4000
 reader=pycolmap.ImageReaderOptions();reader.mask_path=maskdir
 pycolmap.extract_features(DB,images,image_names=names,camera_mode=pycolmap.CameraMode.PER_IMAGE,reader_options=reader,extraction_options=extract,device=pycolmap.Device.cpu)
 with pycolmap.Database.open(DB) as db:
  for n in names:
   im=db.read_image_with_name(n); t=by_name[n]; db.update_camera(pycolmap.Camera(camera_id=im.camera_id,model=pycolmap.CameraModelId.OPENCV,width=int(t['w']),height=int(t['h']),params=np.array([t['fl_x'],t['fl_y'],t['cx'],t['cy'],0,0,0,0],float)))
 matching=pycolmap.FeatureMatchingOptions();matching.use_gpu=False;matching.num_threads=4;matching.max_num_matches=12000;matching.sift.cpu_brute_force_matcher=True
 pair=pycolmap.SequentialPairingOptions();pair.overlap=3;pair.quadratic_overlap=False;pair.loop_detection=False
 verify=pycolmap.TwoViewGeometryOptions();verify.compute_relative_pose=False;verify.ransac.max_error=2.0
 pycolmap.match_sequential(DB,matching,pair,verify,device=pycolmap.Device.cpu)


def main():
 names,by_name,images,masks,transforms,frames=inputs()
 extract_and_match(names,by_name,images,masks)
 r=pycolmap.Reconstruction(); rigs=set()
 with pycolmap.Database.open(DB) as db:
  summary={'images':db.num_images(),'cameras':db.num_cameras(),'keypoints':db.num_keypoints(),'verified_pairs':db.num_verified_image_pairs(),'inlier_matches':db.num_inlier_matches()}
  for n in names:
   im=db.read_image_with_name(n); cam=db.read_camera(im.camera_id);t=by_name[n]
   if cam.model!=pycolmap.CameraModelId.OPENCV or [cam.width,cam.height]!=[t['w'],t['h']] or not np.allclose(cam.params[:4],[t['fl_x'],t['fl_y'],t['cx'],t['cy']]): raise RuntimeError('calibration changed')
   r.add_camera_with_trivial_rig(cam);r.add_image_with_trivial_frame(pycolmap.Image(name=n,camera_id=im.camera_id,image_id=im.image_id),pose(t));rigs.add(im.camera_id)
 tri=pycolmap.IncrementalPipelineOptions();tri.num_threads=4;tri.fix_existing_frames=True;tri.constant_rigs=rigs;tri.mapper.fix_existing_frames=True;tri.mapper.constant_rigs=rigs;tri.ba_refine_focal_length=False;tri.ba_refine_principal_point=False;tri.ba_refine_extra_params=False;tri.ba_use_gpu=False;tri.triangulation.min_angle=1.;tri.triangulation.ignore_two_view_tracks=True
 known=WORK/'known';known.mkdir();r=pycolmap.triangulate_points(r,DB,images,known,clear_points=True,options=tri,refine_intrinsics=False);r.update_point_3d_errors();before=metric(r);r.write(known)
 opt=pycolmap.BundleAdjustmentOptions();opt.refine_focal_length=False;opt.refine_principal_point=False;opt.refine_extra_params=False;opt.refine_rig_from_world=True;opt.refine_sensor_from_rig=False;opt.refine_points3D=True;opt.min_track_length=2;opt.print_summary=False;opt.ceres.use_gpu=False;opt.ceres.loss_function_type=pycolmap.LossFunctionType.HUBER;opt.ceres.loss_function_scale=1.;opt.ceres.solver_options.num_threads=4;opt.ceres.solver_options.max_num_iterations=100
 cfg=pycolmap.BundleAdjustmentConfig();ids=r.reg_image_ids()
 for i in ids: cfg.add_image(i);cfg.set_constant_cam_intrinsics(r.image(i).camera_id)
 for i in r.point3D_ids(): cfg.add_variable_point(i)
 refs=[r.find_image_with_name(names[0]).image_id,r.find_image_with_name(names[1]).image_id]
 for i in refs: cfg.set_constant_rig_from_world_pose(r.image(i).frame_id)
 pycolmap.create_default_bundle_adjuster(opt,cfg,r).solve();r.update_point_3d_errors();after=metric(r);refined=WORK/'refined';refined.mkdir();r.write(refined)
 if OUT.exists(): shutil.rmtree(OUT)
 (OUT/'images').mkdir(parents=True);(OUT/'masks').mkdir()
 accepted=[];outliers=[];newframes=[]
 for n,original in zip(names,frames,strict=True):
  link(images/n,OUT/'images'/n);link(masks/(Path(n).stem+'.png'),OUT/'masks'/(Path(n).stem+'.png'))
  im=r.find_image_with_name(n); observed=im.num_points3D>0
  center=float(np.linalg.norm(im.projection_center()-np.asarray(original['transform_matrix'],float)[:3,3]));rotation=ang(im.cam_from_world().matrix()[:,:3].T@pose(original).matrix()[:,:3])
  use=observed and center<=.5 and rotation<=5
  entry=dict(original)
  if use: entry['transform_matrix']=c2w(im.cam_from_world());accepted.append(n)
  else: outliers.append({'name':n,'observed':observed,'center_change_m':center,'rotation_change_deg':rotation,'reason':'unobserved' if not observed else 'drift_threshold'})
  newframes.append(entry)
 link(DATA/'lidar-seeds.ply',OUT/'lidar-seeds.ply')
 (OUT/'transforms.json').write_text(json.dumps({**transforms,'frames':newframes},indent=2)+'\n')
 audit={'created_at':datetime.now(timezone.utc).isoformat(),'source_dataset':str(DATA.resolve()),'source_pose_mutated':False,'mask_features':'COLMAP reader mask path, source JPEGs unchanged','pose_conversion':'OpenCV world_to_camera = diag(1,-1,-1,1) @ inverse(exported OpenGL camera_to_world)','database':summary,'triangulation_before_ba':before,'bundle_adjustment_after':after,'fixed_reference_images':[names[0],names[1]],'robust_loss':'HUBER scale 1px','accepted_refined_count':len(accepted),'unobserved_or_large_drift_original_count':len(outliers),'outliers':outliers,'models':{'known':str(known),'refined':str(refined)}}
 (OUT/'audit.json').write_text(json.dumps(audit,indent=2)+'\n');(OUT/'provenance.json').write_text(json.dumps({**json.loads((DATA/'provenance.json').read_text()),'camera_poses':'fixed-intrinsics CPU COLMAP refinement; see audit.json','source_dataset':str(DATA.resolve())},indent=2)+'\n')
 print(json.dumps(audit,indent=2))
if __name__=='__main__':main()
