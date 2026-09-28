import { Vector3 } from "three";
import type { SceneGraph } from "@/types/contracts";
import { type MotionPoint, sceneRect } from "./wheelchair-motion";

/**
 * Keeps the orbiting camera out of the walls, the way a game camera does: the
 * camera sits on an arm reaching back from the point it looks at, and when a
 * wall is in the way the arm stops short in front of it instead of passing through.
 *
 * Only walls count. A box the scan misplaced or left floating never pins the
 * camera, and a wall that already contains the point being looked at is
 * ignored, so a bad wall can never leave the owner with no way to move.
 */
export type CameraWall = {
  center: MotionPoint;
  axisX: MotionPoint;
  axisY: MotionPoint;
  halfX: number;
  halfY: number;
  bottom: number;
  top: number;
};

/** How far the lens stays off a wall's face, so the near plane never cuts into it. */
export const WALL_CLEARANCE = 0.2;
const NEAREST_ARM = 0.1;
const PARALLEL = 1e-9;
const RISE_STEPS = 12;
const OVERHEAD = 0.001;

export function cameraWalls(scene: SceneGraph, visibleTop: number): CameraWall[] {
  return scene.nodes.flatMap((node) => {
    if (node.kind !== "wall") return [];
    const rect = sceneRect(node);
    if (!rect) return [];
    const middle = node.transform.m[11];
    return [{
      center: rect.center,
      axisX: rect.axisX,
      axisY: rect.axisY,
      halfX: rect.halfX + WALL_CLEARANCE,
      halfY: rect.halfY + WALL_CLEARANCE,
      bottom: middle - node.dimensions.z / 2 - WALL_CLEARANCE,
      top: Math.min(middle + node.dimensions.z / 2, visibleTop) + WALL_CLEARANCE,
    }];
  });
}

type Slab = { offset: number; step: number; half: number };

function slabsOf(wall: CameraWall, origin: Vector3, direction: Vector3): Slab[] {
  const offset = { x: origin.x - wall.center.x, z: origin.z - wall.center.z };
  const along = (axis: MotionPoint, point: MotionPoint) => point.x * axis.x + point.z * axis.z;
  const flat = { x: direction.x, z: direction.z };
  const middle = (wall.bottom + wall.top) / 2;
  return [
    { offset: along(wall.axisX, offset), step: along(wall.axisX, flat), half: wall.halfX },
    { offset: along(wall.axisY, offset), step: along(wall.axisY, flat), half: wall.halfY },
    { offset: origin.y - middle, step: direction.y, half: (wall.top - wall.bottom) / 2 },
  ];
}

/** How far along the ray it first meets this wall, or Infinity when it never does or starts inside it. */
function entryDistance(wall: CameraWall, origin: Vector3, direction: Vector3): number {
  let enter = -Infinity;
  let exit = Infinity;
  for (const { offset, step, half } of slabsOf(wall, origin, direction)) {
    if (Math.abs(step) < PARALLEL) {
      if (Math.abs(offset) > half) return Infinity;
      continue;
    }
    const [near, far] = [(-half - offset) / step, (half - offset) / step].sort((a, b) => a - b);
    enter = Math.max(enter, near);
    exit = Math.min(exit, far);
  }
  return enter <= exit && enter >= 0 ? enter : Infinity;
}

export function clearDistance(target: Vector3, direction: Vector3, walls: CameraWall[]): number {
  return walls.reduce((nearest, wall) => Math.min(nearest, entryDistance(wall, target, direction)), Infinity);
}

function directionAt(azimuth: number, polar: number): Vector3 {
  return new Vector3(Math.sin(polar) * Math.sin(azimuth), Math.cos(polar), Math.sin(polar) * Math.cos(azimuth));
}

/** The lowest tilt, at the same heading, from which the whole arm clears every wall. Null when even overhead does not. */
function raisedDirection(target: Vector3, direction: Vector3, arm: number, walls: CameraWall[]): Vector3 | null {
  const azimuth = Math.atan2(direction.x, direction.z);
  const clearAt = (polar: number) => clearDistance(target, directionAt(azimuth, polar), walls) >= arm;
  if (!clearAt(OVERHEAD)) return null;
  let [clear, blocked] = [OVERHEAD, Math.acos(Math.min(Math.max(direction.y, -1), 1))];
  for (let step = 0; step < RISE_STEPS; step += 1) {
    const middle = (clear + blocked) / 2;
    if (clearAt(middle)) clear = middle;
    else blocked = middle;
  }
  return directionAt(azimuth, clear);
}

/**
 * Where the camera goes for this heading and arm length. When a wall is in the
 * way, zooming out lifts the camera over the walls into the dollhouse view;
 * anything else pulls it in along its line of sight to just in front of the wall.
 */
export function placeCamera(target: Vector3, position: Vector3, arm: number, walls: CameraWall[], rise: boolean): Vector3 {
  const direction = position.clone().sub(target).normalize();
  const clear = clearDistance(target, direction, walls);
  if (clear >= arm) return target.clone().addScaledVector(direction, arm);
  const raised = rise ? raisedDirection(target, direction, arm, walls) : null;
  if (raised) return target.clone().addScaledVector(raised, arm);
  return target.clone().addScaledVector(direction, Math.max(clear, NEAREST_ARM));
}
