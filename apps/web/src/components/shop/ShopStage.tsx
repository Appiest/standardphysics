"use client";

import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { easing } from "maath";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { WebGLRenderer } from "three";
import { Group, Vector3 } from "three";
import { readThemeColors, type ThemeColors } from "@/lib/themeColors";
import { CustomerRoute } from "./CustomerRoute";
import { OverlapHatch } from "./OverlapHatch";
import { ScanPoints } from "./ScanPoints";
import { ShopModel } from "./ShopModel";
import { shots, type ShotName } from "./shots";
import { StageLevelsProvider, useStageLevels } from "./stageLevels";
import { SweepLight } from "./SweepLight";
import { TapeMeasure } from "./TapeMeasure";

const CAMERA_SMOOTH_SECONDS = 0.95;
const TURNTABLE_RADIANS_PER_SECOND = 0.13;
const SWAY_RADIANS = 0.22;
const SWAY_RADIANS_PER_SECOND = 0.4;
const FULL_TURN = Math.PI * 2;

function CameraRig({ shot }: { shot: ShotName }) {
  const levels = useStageLevels();
  const { camera, size } = useThree();
  const [target] = useState(
    () => new Vector3(...shots.awayBeforeScan.cameraTarget),
  );

  useFrame((_, delta) => {
    const framing = shots[shot];
    easing.damp3(
      camera.position,
      framing.cameraPosition,
      CAMERA_SMOOTH_SECONDS,
      delta,
    );
    easing.damp3(target, framing.cameraTarget, CAMERA_SMOOTH_SECONDS, delta);
    camera.lookAt(target);
    camera.setViewOffset(
      size.width,
      size.height,
      -levels.frameShift * size.width,
      -levels.frameDrop * size.height,
      size.width,
      size.height,
    );
  });

  return null;
}

function Turntable({ children }: { children: ReactNode }) {
  const levels = useStageLevels();
  const orbit = useRef<Group>(null);
  const sway = useRef<Group>(null);

  useFrame(({ clock }, delta) => {
    if (!orbit.current || !sway.current) return;
    const rotation = orbit.current.rotation;
    const nearestRest = Math.round(rotation.y / FULL_TURN) * FULL_TURN;
    rotation.y += TURNTABLE_RADIANS_PER_SECOND * levels.turntable * delta;
    rotation.y +=
      (nearestRest - rotation.y) *
      (1 - levels.turntable) *
      (1 - Math.exp(-2.5 * delta));
    sway.current.rotation.y =
      Math.sin(clock.elapsedTime * SWAY_RADIANS_PER_SECOND) *
      SWAY_RADIANS *
      levels.sway;
  });

  return (
    <group ref={orbit}>
      <group ref={sway}>{children}</group>
    </group>
  );
}

function StageLights({ colors }: { colors: ThemeColors }) {
  return (
    <>
      <hemisphereLight args={[colors.clay, colors.floor, 1.65]} />
      <directionalLight
        position={[-7, 12, 6]}
        intensity={1.35}
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-radius={5}
        shadow-bias={-0.0004}
        shadow-normalBias={0.02}
      >
        <orthographicCamera
          attach="shadow-camera"
          args={[-8, 8, 8, -8, 0.5, 40]}
        />
      </directionalLight>
    </>
  );
}

const REMOUNT_AFTER_LOSS_MS = 400;
const MAX_REMOUNTS_IN_A_ROW = 3;
const HEALTHY_AFTER_MS = 10_000;

/** A lost context hands three a null shader, so nothing is drawn until the stage remounts on a fresh one. */
function skipRenderingWhileLost(gl: WebGLRenderer) {
  const context = gl.getContext();
  const render = gl.render.bind(gl);
  gl.render = (scene, camera) => {
    if (!context.isContextLost()) render(scene, camera);
  };
}

/**
 * Remounts the stage after the browser takes its WebGL context, backing off each time. When the browser keeps
 * refusing, usually because too many contexts are open, the stage stays down and the slides carry on without it.
 */
function useRecoverFromContextLoss() {
  const [generation, setGeneration] = useState(0);
  const [lost, setLost] = useState(false);
  const failuresInARow = useRef(0);

  useEffect(() => {
    if (lost) return;
    const timer = window.setTimeout(() => (failuresInARow.current = 0), HEALTHY_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [lost, generation]);

  useEffect(() => {
    if (!lost || failuresInARow.current > MAX_REMOUNTS_IN_A_ROW) return;
    const timer = window.setTimeout(() => {
      setGeneration((current) => current + 1);
      setLost(false);
    }, REMOUNT_AFTER_LOSS_MS * 2 ** failuresInARow.current);
    return () => window.clearTimeout(timer);
  }, [lost]);

  const watch = useCallback((gl: WebGLRenderer, stopRendering: () => void) => {
    const markLost = () => {
      stopRendering();
      failuresInARow.current += 1;
      setLost(true);
    };
    skipRenderingWhileLost(gl);
    if (gl.getContext().isContextLost()) {
      markLost();
      return;
    }
    gl.domElement.addEventListener(
      "webglcontextlost",
      (event) => {
        event.preventDefault();
        markLost();
      },
      { once: true },
    );
  }, []);

  return { generation, lost, watch };
}

export function ShopStage({ shot }: { shot: ShotName }) {
  const [colors] = useState(readThemeColors);
  const { generation, lost, watch } = useRecoverFromContextLoss();

  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 z-10">
      {!lost && (
        <Canvas
          key={generation}
          flat
          shadows="percentage"
          dpr={[1, 2]}
          gl={{ antialias: true, alpha: true }}
          camera={{
            fov: 24,
            near: 0.1,
            far: 120,
            position: shots.awayBeforeScan.cameraPosition,
          }}
          onCreated={({ gl, setFrameloop }) => {
            gl.localClippingEnabled = true;
            watch(gl, () => setFrameloop("never"));
          }}
        >
          <StageLevelsProvider shot={shot}>
            <CameraRig shot={shot} />
            <StageLights colors={colors} />
            <Turntable>
              <ShopModel colors={colors} />
              <ScanPoints colors={colors} />
              <SweepLight colors={colors} />
              <OverlapHatch colors={colors} />
              <TapeMeasure colors={colors} />
              <CustomerRoute colors={colors} />
            </Turntable>
          </StageLevelsProvider>
        </Canvas>
      )}
    </div>
  );
}
