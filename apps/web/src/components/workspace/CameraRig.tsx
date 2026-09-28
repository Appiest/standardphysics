"use client";

import { OrbitControls } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { useCallback, useEffect, useMemo, useRef } from "react";
import { Box3, PerspectiveCamera, Vector3 } from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { easeOutCubic, fitPoseToBounds, TWEEN_MS, type ViewerPose } from "@/lib/camera";
import { type CameraWall, placeCamera } from "@/lib/camera-walls";

type Tween = { from: ViewerPose; to: ViewerPose; startedAt: number };
/** The arm the owner asked for with their last zoom, and the one the walls allowed. */
type Arm = { wanted: number; placed: number };

const ZOOM_TOLERANCE = 1e-4;

function armAfterChange(arm: Arm, distance: number, minDistance: number): { wanted: number; rise: boolean } {
  const expected = Math.max(arm.placed, minDistance);
  if (Math.abs(distance - expected) <= ZOOM_TOLERANCE) return { wanted: arm.wanted, rise: false };
  return { wanted: distance, rise: distance > expected };
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function currentPose(camera: PerspectiveCamera, controls: OrbitControlsImpl): ViewerPose {
  return {
    position: camera.position.toArray() as ViewerPose["position"],
    target: controls.target.toArray() as ViewerPose["target"],
    fov: camera.fov,
  };
}

function applyPose(camera: PerspectiveCamera, controls: OrbitControlsImpl, from: ViewerPose, to: ViewerPose, t: number) {
  camera.position.lerpVectors(new Vector3(...from.position), new Vector3(...to.position), t);
  controls.target.lerpVectors(new Vector3(...from.target), new Vector3(...to.target), t);
  camera.fov = from.fov + (to.fov - from.fov) * t;
  camera.updateProjectionMatrix();
  controls.update();
}

/**
 * Slides the picture right by some pixels without moving the camera, so a panel
 * laid over the canvas's left edge doesn't sit on the middle of the shop. Orbiting
 * and picking still work, because the shift lives in the projection.
 */
function useFrameShift(pixels: number) {
  const camera = useThree((state) => state.camera) as PerspectiveCamera;
  const size = useThree((state) => state.size);
  const invalidate = useThree((state) => state.invalidate);
  useEffect(() => {
    if (pixels === 0) return;
    camera.setViewOffset(size.width, size.height, -pixels, 0, size.width, size.height);
    invalidate();
    return () => {
      camera.clearViewOffset();
      invalidate();
    };
  }, [camera, size.width, size.height, pixels, invalidate]);
}

/** Orbit controls that stop short of walls, plus a 700 ms ease-out flight whenever the requested pose changes. */
export function CameraRig({ pose: requestedPose, bounds, locked = false, zoom, walls = [], frameShift = 0 }: { pose: ViewerPose; bounds?: Box3 | null; locked?: boolean; zoom?: { min: number; max: number }; walls?: CameraWall[]; frameShift?: number }) {
  const controls = useRef<OrbitControlsImpl>(null);
  const tween = useRef<Tween | null>(null);
  const arm = useRef<Arm>({ wanted: 0, placed: 0 });
  const camera = useThree((state) => state.camera) as PerspectiveCamera;
  const invalidate = useThree((state) => state.invalidate);
  const size = useThree((state) => state.size);
  const pose = useMemo(() => bounds ? fitPoseToBounds(requestedPose, bounds, size.width / size.height) : requestedPose,
    [requestedPose, bounds, size.width, size.height]);

  useEffect(() => {
    const orbit = controls.current;
    if (!orbit) return;
    if (prefersReducedMotion()) {
      applyPose(camera, orbit, pose, pose, 1);
      const reached = camera.position.distanceTo(orbit.target);
      arm.current = { wanted: reached, placed: reached };
      invalidate();
      return;
    }
    tween.current = { from: currentPose(camera, orbit), to: pose, startedAt: performance.now() };
    orbit.enabled = false;
    invalidate();
  }, [pose, camera, invalidate]);

  useFrame(() => {
    const active = tween.current;
    const orbit = controls.current;
    if (!active || !orbit) return;
    const t = easeOutCubic((performance.now() - active.startedAt) / TWEEN_MS);
    applyPose(camera, orbit, active.from, active.to, t);
    if (t >= 1) {
      tween.current = null;
      const reached = camera.position.distanceTo(orbit.target);
      arm.current = { wanted: reached, placed: reached };
      orbit.enabled = !locked;
      return;
    }
    invalidate();
  });

  useEffect(() => {
    if (controls.current && !tween.current) controls.current.enabled = !locked;
  }, [locked]);

  useFrameShift(frameShift);

  const keepOffWalls = useCallback(() => {
    const orbit = controls.current;
    if (!orbit || tween.current || walls.length === 0) return;
    const { wanted, rise } = armAfterChange(arm.current, camera.position.distanceTo(orbit.target), orbit.minDistance);
    camera.position.copy(placeCamera(orbit.target, camera.position, wanted, walls, rise));
    camera.lookAt(orbit.target);
    arm.current = { wanted, placed: camera.position.distanceTo(orbit.target) };
  }, [camera, walls]);

  return (
    <OrbitControls
      ref={controls}
      onChange={keepOffWalls}
      makeDefault
      enableDamping={false}
      maxPolarAngle={Math.PI / 2 - 0.05}
      minDistance={zoom?.min}
      maxDistance={zoom?.max}
    />
  );
}
