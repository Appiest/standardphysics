import type { SceneNode } from "@/types/contracts";

import { EPSILON, add, dot, length, normalize, scale, subtract, type MotionPoint } from "./motion-vector";

export type CollisionRect = {
  node: SceneNode;
  center: MotionPoint;
  axisX: MotionPoint;
  axisY: MotionPoint;
  halfX: number;
  halfY: number;
};

export type WheelchairMotionGeometry = {
  floors: CollisionRect[];
  obstacles: CollisionRect[];
  targets: CollisionRect[];
};

const MIN_SEGMENT = 0.01;
// Some scene exports describe walls and portals as planar outlines. This only gives
// those navigation boundaries a small collision thickness; measured widths remain intact.
const PLANAR_BOUNDARY_THICKNESS = 0.05;

type ProjectedAxis = {
  point: MotionPoint;
  dimension: number;
  scale: number;
};

/**
 * Scene exports may encode a horizontal floor in local X/Y or local X/Z.
 * Select the two local axes that actually span the viewer ground plane rather
 * than assuming a fixed source-up axis.
 */
function horizontalAxes(node: SceneNode, planarBoundary: boolean): ProjectedAxis[] {
  const matrix = node.transform.m;
  const fallbackDimension = planarBoundary ? PLANAR_BOUNDARY_THICKNESS : 0;
  const axes = [
    { point: { x: matrix[0], z: -matrix[4] }, dimension: node.dimensions.x },
    { point: { x: matrix[1], z: -matrix[5] }, dimension: node.dimensions.y },
    { point: { x: matrix[2], z: -matrix[6] }, dimension: node.dimensions.z },
  ].map((axis) => ({ ...axis, scale: length(axis.point), dimension: axis.dimension > EPSILON ? axis.dimension : fallbackDimension }));

  return axes
    .filter((axis) => axis.scale > EPSILON && axis.dimension > EPSILON)
    .sort((left, right) => right.scale * right.dimension - left.scale * left.dimension)
    .slice(0, 2);
}

function sceneRect(node: SceneNode): CollisionRect | null {
  const matrix = node.transform.m;
  const planarBoundary = node.kind === "wall" || node.kind === "door" || node.kind === "opening";
  const axes = horizontalAxes(node, planarBoundary);
  if (axes.length !== 2) return null;
  const [x, y] = axes;
  const halfX = (x.dimension * x.scale) / 2;
  const halfY = (y.dimension * y.scale) / 2;

  if (halfX <= EPSILON || halfY <= EPSILON) return null;

  return {
    node,
    center: { x: matrix[3], z: -matrix[7] },
    axisX: normalize(x.point, { x: 1, z: 0 }),
    axisY: normalize(y.point, { x: 0, z: 1 }),
    halfX,
    halfY,
  };
}

function portalIntervals(wall: CollisionRect, portals: CollisionRect[]): { axis: "x" | "y"; intervals: { start: number; end: number }[] } {
  const axis = wall.halfX >= wall.halfY ? "x" : "y";
  const wallAxis = axis === "x" ? wall.axisX : wall.axisY;
  const acrossAxis = axis === "x" ? wall.axisY : wall.axisX;
  const wallHalfAlong = axis === "x" ? wall.halfX : wall.halfY;
  const wallHalfAcross = axis === "x" ? wall.halfY : wall.halfX;

  const intervals = portals.filter((portal) => portal.node.parent_id === null || portal.node.parent_id === wall.node.id).flatMap((portal) => {
    const offset = subtract(portal.center, wall.center);
    const centerAlong = dot(offset, wallAxis);
    const centerAcross = dot(offset, acrossAxis);
    const portalHalfAlong = Math.abs(dot(portal.axisX, wallAxis)) * portal.halfX + Math.abs(dot(portal.axisY, wallAxis)) * portal.halfY;
    const portalHalfAcross = Math.abs(dot(portal.axisX, acrossAxis)) * portal.halfX + Math.abs(dot(portal.axisY, acrossAxis)) * portal.halfY;

    if (Math.abs(centerAcross) > wallHalfAcross + portalHalfAcross + EPSILON) return [];
    const start = Math.max(-wallHalfAlong, centerAlong - portalHalfAlong);
    const end = Math.min(wallHalfAlong, centerAlong + portalHalfAlong);
    if (end - start <= MIN_SEGMENT) return [];
    const opening = { start, end };
    return opening.end - opening.start > MIN_SEGMENT ? [opening] : [];
  }).sort((left, right) => left.start - right.start);

  const merged = intervals.reduce<{ start: number; end: number }[]>((all, next) => {
    const previous = all[all.length - 1];
    if (previous && next.start <= previous.end + EPSILON) {
      previous.end = Math.max(previous.end, next.end);
      return all;
    }
    all.push({ ...next });
    return all;
  }, []);

  return { axis, intervals: merged };
}

function splitWall(wall: CollisionRect, portals: CollisionRect[]): CollisionRect[] {
  const { axis, intervals } = portalIntervals(wall, portals);
  if (intervals.length === 0) return [wall];

  const wallAxis = axis === "x" ? wall.axisX : wall.axisY;
  const wallHalfAlong = axis === "x" ? wall.halfX : wall.halfY;
  const segments: { start: number; end: number }[] = [];
  let cursor = -wallHalfAlong;

  for (const opening of intervals) {
    if (opening.start - cursor > MIN_SEGMENT) segments.push({ start: cursor, end: opening.start });
    cursor = Math.max(cursor, opening.end);
  }
  if (wallHalfAlong - cursor > MIN_SEGMENT) segments.push({ start: cursor, end: wallHalfAlong });

  return segments.map((segment) => {
    const halfAlong = (segment.end - segment.start) / 2;
    const center = add(wall.center, scale(wallAxis, (segment.start + segment.end) / 2));
    return axis === "x"
      ? { ...wall, center, halfX: halfAlong }
      : { ...wall, center, halfY: halfAlong };
  });
}

/** Builds viewer-plane collision rectangles from scene coordinates. Floors and portals stay passable. */
export function wheelchairMotionGeometry(nodes: SceneNode[]): WheelchairMotionGeometry {
  const rects = nodes.flatMap((node) => {
    const rect = sceneRect(node);
    return rect ? [rect] : [];
  });
  const portals = rects.filter((rect) => rect.node.kind === "door" || rect.node.kind === "opening");
  const walls = rects.filter((rect) => rect.node.kind === "wall").flatMap((wall) => splitWall(wall, portals));
  const targets = rects.filter((rect) => rect.node.kind === "object" || rect.node.kind === "outlet" || rect.node.kind === "candidate_outlet");
  const objects = targets.filter((rect) => rect.node.dimensions.z > EPSILON && rect.node.kind === "object");

  return {
    floors: rects.filter((rect) => rect.node.kind === "floor"),
    obstacles: [...walls, ...objects],
    targets,
  };
}

export function closestPoint(rect: CollisionRect, point: MotionPoint): MotionPoint {
  const offset = subtract(point, rect.center);
  const localX = Math.max(-rect.halfX, Math.min(rect.halfX, dot(offset, rect.axisX)));
  const localY = Math.max(-rect.halfY, Math.min(rect.halfY, dot(offset, rect.axisY)));
  return add(rect.center, add(scale(rect.axisX, localX), scale(rect.axisY, localY)));
}

export function distanceToRect(point: MotionPoint, rect: CollisionRect): number {
  return length(subtract(point, closestPoint(rect, point)));
}

export function collidesAt(point: MotionPoint, obstacles: CollisionRect[], radius: number): boolean {
  return obstacles.some((obstacle) => distanceToRect(point, obstacle) < radius - EPSILON);
}

/** Tests if a 2D line segment between a and b penetrates a collision rectangle. */
export function segmentIntersectsRect(a: MotionPoint, b: MotionPoint, rect: CollisionRect): boolean {
  const da = subtract(a, rect.center);
  const db = subtract(b, rect.center);
  const p1x = dot(da, rect.axisX);
  const p1y = dot(da, rect.axisY);
  const p2x = dot(db, rect.axisX);
  const p2y = dot(db, rect.axisY);

  const dx = p2x - p1x;
  const dy = p2y - p1y;

  let t0 = 0.0;
  let t1 = 1.0;

  const clip = (p: number, q: number) => {
    if (Math.abs(p) < EPSILON) {
      if (q < 0) return false;
      return true;
    }
    const r = q / p;
    if (p < 0) {
      if (r > t1) return false;
      if (r > t0) t0 = r;
    } else {
      if (r < t0) return false;
      if (r < t1) t1 = r;
    }
    return true;
  };

  // Shrink slightly so a point terminating on the boundary face of a supporting wall does not penetrate
  const shrinkX = Math.max(0, rect.halfX - 0.005);
  const shrinkY = Math.max(0, rect.halfY - 0.005);

  if (!clip(-dx, p1x - (-shrinkX))) return false;
  if (!clip(dx, shrinkX - p1x)) return false;
  if (!clip(-dy, p1y - (-shrinkY))) return false;
  if (!clip(dy, shrinkY - p1y)) return false;

  return t0 <= t1 && t1 - t0 > 0.001;
}


export function containsPoint(rect: CollisionRect, point: MotionPoint, inset = 0): boolean {
  const offset = subtract(point, rect.center);
  return Math.abs(dot(offset, rect.axisX)) <= rect.halfX - inset && Math.abs(dot(offset, rect.axisY)) <= rect.halfY - inset;
}

