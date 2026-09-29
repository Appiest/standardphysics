"use client";

import { OrbitControls } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import { Box3, PerspectiveCamera, Vector3 } from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { easeOutCubic, fitPoseToBounds, samePose, TWEEN_MS, type ViewerPose } from "@/lib/camera";

type Tween = { from: ViewerPose; to: ViewerPose; startedAt: number };
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

/** Orbit controls, plus a 700 ms ease-out flight whenever the requested pose changes. */
export function CameraRig({ pose: requestedPose, bounds, locked = false, zoom, frameShift = 0 }: { pose: ViewerPose; bounds?: Box3 | null; locked?: boolean; zoom?: { min: number; max: number }; frameShift?: number }) {
  const controls = useRef<OrbitControlsImpl>(null);
  const tween = useRef<Tween | null>(null);
  /** The last pose flown to, so the same pose handed over again by a refreshed page leaves the camera where the owner put it. */
  const requested = useRef<ViewerPose | null>(null);
  const camera = useThree((state) => state.camera) as PerspectiveCamera;
  const invalidate = useThree((state) => state.invalidate);
  const size = useThree((state) => state.size);
  const pose = useMemo(() => bounds ? fitPoseToBounds(requestedPose, bounds, size.width / size.height) : requestedPose,
    [requestedPose, bounds, size.width, size.height]);

  useEffect(() => {
    const orbit = controls.current;
    if (!orbit) return;
    if (requested.current && samePose(requested.current, pose)) return;
    requested.current = pose;
    if (prefersReducedMotion()) {
      applyPose(camera, orbit, pose, pose, 1);
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
      orbit.enabled = !locked;
      return;
    }
    invalidate();
  });

  useEffect(() => {
    if (controls.current && !tween.current) controls.current.enabled = !locked;
  }, [locked]);

  useFrameShift(frameShift);

  return (
    <OrbitControls
      ref={controls}
      makeDefault
      enableDamping={false}
      maxPolarAngle={Math.PI / 2 - 0.05}
      minDistance={zoom?.min}
      maxDistance={zoom?.max}
    />
  );
}
