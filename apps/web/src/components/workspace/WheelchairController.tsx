"use client";

import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import { MathUtils, Vector3 } from "three";
import type { SceneGraph, SceneNode } from "@/types/contracts";
import {
  distanceToRect,
  dockPoint,
  sweepWheelchairInGeometry,
  wheelchairMotionGeometry,
  wheelchairSpawn,
  type MotionPoint,
  type WheelchairProfile,
} from "@/lib/wheelchair-motion";

const TURN_SPEED = 1.8;
const REACH_LIMIT = 2.4;
const STATE_INTERVAL_SECONDS = 0.1;

export type WheelchairState = {
  x: number;
  z: number;
  yaw: number;
  speed: number;
  targetedNode: SceneNode | null;
  reachDistance: number;
};

type WheelchairControllerProps = {
  active: boolean;
  scene: SceneGraph;
  onStateChange: (state: WheelchairState) => void;
  dockTarget: SceneNode | null;
  onClearDock: () => void;
  onSelectNode: (node: SceneNode) => void;
  profile: WheelchairProfile;
  onExit?: () => void;
  initialPosition?: [number, number, number];
};

function navigationKey(code: string): boolean {
  return ["KeyW", "KeyS", "KeyA", "KeyD", "KeyQ", "KeyE", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(code);
}

function typingTarget(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean(target.closest("input, textarea, select, [contenteditable='true']"));
}

function stateChanged(previous: WheelchairState, next: WheelchairState): boolean {
  return previous.targetedNode?.id !== next.targetedNode?.id
    || Math.hypot(previous.x - next.x, previous.z - next.z) > 0.01
    || Math.abs(previous.yaw - next.yaw) > 1
    || Math.abs(previous.speed - next.speed) > 0.1
    || Math.abs(previous.reachDistance - next.reachDistance) > 0.05;
}

export function WheelchairController({
  active,
  scene,
  onStateChange,
  dockTarget,
  onClearDock,
  onSelectNode,
  profile,
  onExit,
  initialPosition = [-3.0, 0, -3.0],
}: WheelchairControllerProps) {
  const { camera, invalidate } = useThree();
  const pos = useRef(new Vector3(initialPosition[0], profile.eyeHeight, initialPosition[2]));
  const yaw = useRef(0);
  const keys = useRef<Record<string, boolean>>({});
  const started = useRef(false);
  const shownScene = useRef<SceneGraph | null>(null);
  const appliedRadius = useRef<number | null>(null);
  const lastReport = useRef<{ at: number; state: WheelchairState } | null>(null);
  const geometry = useMemo(() => wheelchairMotionGeometry(scene.nodes), [scene.nodes]);

  // eslint-disable-next-line complexity
  useEffect(() => {
    if (!active) {
      keys.current = {};
      started.current = false;
      appliedRadius.current = null;
      return;
    }
    if (!started.current || shownScene.current !== scene || appliedRadius.current !== profile.collisionRadius) {
      const preferred = started.current && shownScene.current === scene
        ? { x: pos.current.x, z: pos.current.z }
        : { x: initialPosition[0], z: initialPosition[2] };
      const spawn = wheelchairSpawn(preferred, geometry, profile.collisionRadius);
      if (!spawn) {
        started.current = false;
        onExit?.();
        return;
      }
      pos.current.set(spawn.x, profile.eyeHeight, spawn.z);
      if (!started.current || shownScene.current !== scene) yaw.current = 0;
      lastReport.current = null;
      shownScene.current = scene;
      appliedRadius.current = profile.collisionRadius;
      started.current = true;
    }
    pos.current.y = profile.eyeHeight;
  }, [active, geometry, initialPosition, onExit, profile.collisionRadius, profile.eyeHeight, scene]);

  useEffect(() => {
    if (!active) return;

    const clearKeys = () => {
      keys.current = {};
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (typingTarget(event.target)) return;
      if (event.code === "Escape") {
        event.preventDefault();
        clearKeys();
        onExit?.();
        return;
      }
      if (!navigationKey(event.code)) return;
      event.preventDefault();
      keys.current[event.code] = true;
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (typingTarget(event.target) || !navigationKey(event.code)) return;
      event.preventDefault();
      keys.current[event.code] = false;
    };
    const onVisibilityChange = () => {
      if (document.visibilityState !== "visible") clearKeys();
    };
    const onFocusIn = (event: FocusEvent) => {
      if (typingTarget(event.target)) clearKeys();
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", clearKeys);
    window.addEventListener("focusin", onFocusIn);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", clearKeys);
      window.removeEventListener("focusin", onFocusIn);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      clearKeys();
    };
  }, [active, onExit]);

  useEffect(() => {
    if (!active || !dockTarget) return;
    const target = geometry.targets.find((rect) => rect.node.id === dockTarget.id);
    if (target) {
      const from: MotionPoint = { x: pos.current.x, z: pos.current.z };
      const destination = dockPoint(from, target, profile.collisionRadius);
      const movement = sweepWheelchairInGeometry(from, { x: destination.x - from.x, z: destination.z - from.z }, geometry, profile.collisionRadius);
      pos.current.x = movement.point.x;
      pos.current.z = movement.point.z;
      if (movement.reached) {
        yaw.current = Math.atan2(target.center.x - pos.current.x, target.center.z - pos.current.z) + Math.PI;
        onSelectNode(dockTarget);
      }
    }
    onClearDock();
  }, [active, dockTarget, geometry, onClearDock, onSelectNode, profile.collisionRadius]);

  // eslint-disable-next-line complexity
  useFrame((state, delta) => {
    if (!active) return;

    const dt = Math.min(delta, 0.1);
    let moving = 0;
    let turning = 0;
    let strafing = 0;
    if (keys.current.KeyW || keys.current.ArrowUp) moving += 1;
    if (keys.current.KeyS || keys.current.ArrowDown) moving -= 1;
    if (keys.current.KeyA || keys.current.ArrowLeft) turning += 1;
    if (keys.current.KeyD || keys.current.ArrowRight) turning -= 1;
    if (keys.current.KeyQ) strafing -= 1;
    if (keys.current.KeyE) strafing += 1;
    if (turning !== 0) yaw.current += turning * TURN_SPEED * dt;

    const forwardX = -Math.sin(yaw.current);
    const forwardZ = -Math.cos(yaw.current);
    const rightX = Math.cos(yaw.current);
    const rightZ = -Math.sin(yaw.current);
    const intended = {
      x: (forwardX * moving + rightX * strafing) * profile.speed * dt,
      z: (forwardZ * moving + rightZ * strafing) * profile.speed * dt,
    };
    const before = { x: pos.current.x, z: pos.current.z };
    const swept = sweepWheelchairInGeometry(before, intended, geometry, profile.collisionRadius);
    pos.current.set(swept.point.x, profile.eyeHeight, swept.point.z);

    camera.position.copy(pos.current);
    camera.lookAt(pos.current.x + forwardX * 5, pos.current.y, pos.current.z + forwardZ * 5);

    let closestNode: SceneNode | null = null;
    let minReach = REACH_LIMIT;
    for (const target of geometry.targets) {
      const dx = target.center.x - pos.current.x;
      const dz = target.center.z - pos.current.z;
      const centerDistance = Math.hypot(dx, dz);
      const distance = distanceToRect(pos.current, target);
      const facing = (forwardX * dx + forwardZ * dz) / Math.max(centerDistance, 0.001);
      if (distance < minReach && facing > 0.5) {
        closestNode = target.node;
        minReach = distance;
      }
    }

    const actual = { x: pos.current.x - before.x, z: pos.current.z - before.z };
    const nextState: WheelchairState = {
      x: pos.current.x,
      z: pos.current.z,
      yaw: MathUtils.radToDeg(yaw.current),
      speed: Math.hypot(actual.x, actual.z) / Math.max(dt, 0.001),
      targetedNode: closestNode,
      reachDistance: minReach,
    };
    const previousReport = lastReport.current;
    const targetChanged = previousReport?.state.targetedNode?.id !== nextState.targetedNode?.id;
    if (!previousReport || targetChanged || (state.clock.elapsedTime - previousReport.at >= STATE_INTERVAL_SECONDS && stateChanged(previousReport.state, nextState))) {
      onStateChange(nextState);
      lastReport.current = { at: state.clock.elapsedTime, state: nextState };
    }
    invalidate();
  });

  return null;
}
