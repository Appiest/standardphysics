import type { SceneNode } from "@/types/contracts";

import { closestPoint, distanceToRect, segmentIntersectsRect, type CollisionRect, type WheelchairMotionGeometry } from "./wheelchair-geometry";
import { EPSILON, add, dot, length, normalize, scale, subtract, type MotionPoint } from "./motion-vector";

export const LOOK_LIMIT = 4.0;
/** When the centre of the view crosses nothing, the nearest thing to it within this angle is named instead. */
const AIM_CONE_COS = Math.cos((15 * Math.PI) / 180);

export type TargetInView = {
  node: SceneNode;
  distance: number;
};

type Interval = { enter: number; exit: number };

function slab(start: number, step: number, half: number): Interval | null {
  if (Math.abs(step) < EPSILON) return Math.abs(start) <= half ? { enter: -Infinity, exit: Infinity } : null;
  const first = (-half - start) / step;
  const second = (half - start) / step;
  return { enter: Math.min(first, second), exit: Math.max(first, second) };
}

/** How far along the ray it first touches the rectangle, or null when it misses. */
export function rayEntryDistance(origin: MotionPoint, direction: MotionPoint, rect: CollisionRect): number | null {
  const offset = subtract(origin, rect.center);
  const across = slab(dot(offset, rect.axisX), dot(direction, rect.axisX), rect.halfX);
  const along = slab(dot(offset, rect.axisY), dot(direction, rect.axisY), rect.halfY);
  if (!across || !along) return null;
  const enter = Math.max(across.enter, along.enter, 0);
  const exit = Math.min(across.exit, along.exit);
  return enter <= exit ? enter : null;
}

function hiddenByWall(from: MotionPoint, to: MotionPoint, walls: CollisionRect[]): boolean {
  return walls.some((wall) => segmentIntersectsRect(from, to, wall));
}

function targetOnAxis(point: MotionPoint, forward: MotionPoint, targets: CollisionRect[], walls: CollisionRect[]): CollisionRect | null {
  let nearest: CollisionRect | null = null;
  let nearestDistance = LOOK_LIMIT;
  for (const target of targets) {
    const distance = rayEntryDistance(point, forward, target);
    if (distance === null || distance > nearestDistance) continue;
    if (hiddenByWall(point, add(point, scale(forward, distance)), walls)) continue;
    nearest = target;
    nearestDistance = distance;
  }
  return nearest;
}

function targetNearAxis(point: MotionPoint, forward: MotionPoint, targets: CollisionRect[], walls: CollisionRect[]): CollisionRect | null {
  let closest: CollisionRect | null = null;
  let closestCos = AIM_CONE_COS;
  for (const target of targets) {
    const nearestPoint = closestPoint(target, point);
    const toTarget = subtract(nearestPoint, point);
    const distance = length(toTarget);
    if (distance > LOOK_LIMIT || distance < EPSILON) continue;
    const cosine = dot(toTarget, forward) / distance;
    if (cosine < closestCos || hiddenByWall(point, nearestPoint, walls)) continue;
    closest = target;
    closestCos = cosine;
  }
  return closest;
}

/**
 * The thing at the centre of the view: the first target the view's centre line
 * reaches without passing through a wall, or failing that, the one nearest that line.
 */
export function targetInView(point: MotionPoint, forward: MotionPoint, geometry: WheelchairMotionGeometry): TargetInView | null {
  const direction = normalize(forward, { x: 0, z: -1 });
  const walls = geometry.obstacles.filter((rect) => rect.node.kind === "wall");
  const target = targetOnAxis(point, direction, geometry.targets, walls) ?? targetNearAxis(point, direction, geometry.targets, walls);
  return target ? { node: target.node, distance: distanceToRect(point, target) } : null;
}
