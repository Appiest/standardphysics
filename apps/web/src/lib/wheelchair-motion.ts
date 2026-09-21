import type { SceneNode } from "@/types/contracts";

export type MotionPoint = { x: number; z: number };

/** Adjustable navigation estimates, not a model-specific wheelchair measurement. */
export type WheelchairProfile = {
  eyeHeight: number;
  speed: number;
  collisionRadius: number;
};

export const WHEELCHAIR_PROFILE_LIMITS = {
  eyeHeight: { min: 0.75, max: 1.45 },
  speed: { min: 0.6, max: 4 },
  collisionRadius: { min: 0.3, max: 0.7 },
} as const;

export const DEFAULT_WHEELCHAIR_PROFILE: WheelchairProfile = {
  eyeHeight: 1.15,
  speed: 2.4,
  collisionRadius: 0.45,
};

function boundedEstimate(value: unknown, limits: { min: number; max: number }, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback;
  return Math.min(limits.max, Math.max(limits.min, value));
}

/** Keeps user-entered navigation estimates finite and within the conservative UI range. */
export function wheelchairProfile(profile: Partial<WheelchairProfile> | null | undefined): WheelchairProfile {
  return {
    eyeHeight: boundedEstimate(profile?.eyeHeight, WHEELCHAIR_PROFILE_LIMITS.eyeHeight, DEFAULT_WHEELCHAIR_PROFILE.eyeHeight),
    speed: boundedEstimate(profile?.speed, WHEELCHAIR_PROFILE_LIMITS.speed, DEFAULT_WHEELCHAIR_PROFILE.speed),
    collisionRadius: boundedEstimate(profile?.collisionRadius, WHEELCHAIR_PROFILE_LIMITS.collisionRadius, DEFAULT_WHEELCHAIR_PROFILE.collisionRadius),
  };
}

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

export type SweptMove = {
  point: MotionPoint;
  reached: boolean;
};

const EPSILON = 0.0001;
const MIN_SEGMENT = 0.01;
// Some scene exports describe walls and portals as planar outlines. This only gives
// those navigation boundaries a small collision thickness; measured widths remain intact.
const PLANAR_BOUNDARY_THICKNESS = 0.05;

function add(a: MotionPoint, b: MotionPoint): MotionPoint {
  return { x: a.x + b.x, z: a.z + b.z };
}

function subtract(a: MotionPoint, b: MotionPoint): MotionPoint {
  return { x: a.x - b.x, z: a.z - b.z };
}

function scale(point: MotionPoint, amount: number): MotionPoint {
  return { x: point.x * amount, z: point.z * amount };
}

function dot(a: MotionPoint, b: MotionPoint): number {
  return a.x * b.x + a.z * b.z;
}

function length(point: MotionPoint): number {
  return Math.hypot(point.x, point.z);
}

function normalize(point: MotionPoint, fallback: MotionPoint): MotionPoint {
  const magnitude = length(point);
  return magnitude > EPSILON ? scale(point, 1 / magnitude) : fallback;
}

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
  const targets = rects.filter((rect) => rect.node.kind === "object");
  const objects = targets.filter((rect) => rect.node.dimensions.z > EPSILON);

  return {
    floors: rects.filter((rect) => rect.node.kind === "floor"),
    obstacles: [...walls, ...objects],
    targets,
  };
}

function closestPoint(rect: CollisionRect, point: MotionPoint): MotionPoint {
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

function sweep(start: MotionPoint, delta: MotionPoint, radius: number, canOccupy: (point: MotionPoint) => boolean, maxStep: number): SweptMove {
  if (!canOccupy(start)) return { point: start, reached: false };
  const total = length(delta);
  if (total <= EPSILON) return { point: start, reached: true };

  const stepSize = Math.min(Math.max(maxStep, EPSILON), Math.max(radius / 2, EPSILON));
  const steps = Math.max(1, Math.ceil(total / stepSize));
  let valid = start;
  for (let step = 1; step <= steps; step += 1) {
    const next = add(start, scale(delta, step / steps));
    if (!canOccupy(next)) return { point: valid, reached: false };
    valid = next;
  }
  return { point: valid, reached: true };
}

/** Moves in bounded increments so a long frame cannot cross a thin wall. */
export function sweepWheelchair(start: MotionPoint, delta: MotionPoint, obstacles: CollisionRect[], radius: number, maxStep = 0.08): SweptMove {
  return sweep(start, delta, radius, (point) => !collidesAt(point, obstacles, radius), maxStep);
}

function containsPoint(rect: CollisionRect, point: MotionPoint, inset = 0): boolean {
  const offset = subtract(point, rect.center);
  return Math.abs(dot(offset, rect.axisX)) <= rect.halfX - inset && Math.abs(dot(offset, rect.axisY)) <= rect.halfY - inset;
}

/** True only for a collision-free point on one of the measured floor surfaces. */
export function canOccupyWheelchair(point: MotionPoint, geometry: WheelchairMotionGeometry, radius: number): boolean {
  const onFloor = geometry.floors.length === 0 || geometry.floors.some((floor) => floor.halfX >= radius && floor.halfY >= radius && containsPoint(floor, point, radius));
  return onFloor && !collidesAt(point, geometry.obstacles, radius);
}

/** Uses the same bounded sweep for manual drive and docking, including floor coverage. */
export function sweepWheelchairInGeometry(start: MotionPoint, delta: MotionPoint, geometry: WheelchairMotionGeometry, radius: number, maxStep = 0.08): SweptMove {
  return sweep(start, delta, radius, (point) => canOccupyWheelchair(point, geometry, radius), maxStep);
}

function floorCandidates(floor: CollisionRect, radius: number): MotionPoint[] {
  const insetX = Math.max(0, floor.halfX - radius);
  const insetY = Math.max(0, floor.halfY - radius);
  return [
    floor.center,
    add(floor.center, add(scale(floor.axisX, insetX * 0.5), scale(floor.axisY, insetY * 0.5))),
    add(floor.center, add(scale(floor.axisX, -insetX * 0.5), scale(floor.axisY, insetY * 0.5))),
    add(floor.center, add(scale(floor.axisX, insetX * 0.5), scale(floor.axisY, -insetY * 0.5))),
    add(floor.center, add(scale(floor.axisX, -insetX * 0.5), scale(floor.axisY, -insetY * 0.5))),
  ];
}

/** Chooses a free point near the requested start, preferring measured floor space. */
export function wheelchairSpawn(preferred: MotionPoint, geometry: WheelchairMotionGeometry, radius: number): MotionPoint | null {
  const candidates: MotionPoint[] = [preferred];
  for (let ring = 0.25; ring <= 2; ring += 0.25) {
    candidates.push(
      { x: preferred.x + ring, z: preferred.z },
      { x: preferred.x - ring, z: preferred.z },
      { x: preferred.x, z: preferred.z + ring },
      { x: preferred.x, z: preferred.z - ring },
    );
  }
  candidates.push(...geometry.floors.flatMap((floor) => floorCandidates(floor, radius)));

  const gridStep = 0.25;
  const addGrid = (center: MotionPoint, axisX: MotionPoint, axisY: MotionPoint, halfX: number, halfY: number) => {
    const xSteps = Math.min(64, Math.floor((halfX * 2) / gridStep));
    const ySteps = Math.min(64, Math.floor((halfY * 2) / gridStep));
    for (let xIndex = 0; xIndex <= xSteps; xIndex += 1) {
      for (let yIndex = 0; yIndex <= ySteps; yIndex += 1) {
        const localX = -halfX + (xIndex / Math.max(xSteps, 1)) * halfX * 2;
        const localY = -halfY + (yIndex / Math.max(ySteps, 1)) * halfY * 2;
        candidates.push(add(center, add(scale(axisX, localX), scale(axisY, localY))));
      }
    }
  };
  if (geometry.floors.length > 0) {
    for (const floor of geometry.floors) {
      if (floor.halfX >= radius && floor.halfY >= radius) addGrid(floor.center, floor.axisX, floor.axisY, floor.halfX - radius, floor.halfY - radius);
    }
  } else {
    addGrid(preferred, { x: 1, z: 0 }, { x: 0, z: 1 }, 2, 2);
  }
  candidates.sort((left, right) => length(subtract(left, preferred)) - length(subtract(right, preferred)));

  return candidates.find((candidate) => canOccupyWheelchair(candidate, geometry, radius)) ?? null;
}

/** Returns a clear point on the side of an object closest to the wheelchair. */
export function dockPoint(from: MotionPoint, target: CollisionRect, radius: number, gap = 0.2): MotionPoint {
  const offset = subtract(from, target.center);
  const localX = dot(offset, target.axisX);
  const localY = dot(offset, target.axisY);
  if (Math.abs(localX) / target.halfX >= Math.abs(localY) / target.halfY) {
    const direction = localX >= 0 ? 1 : -1;
    return add(target.center, scale(target.axisX, direction * (target.halfX + radius + gap)));
  }
  const direction = localY >= 0 ? 1 : -1;
  return add(target.center, scale(target.axisY, direction * (target.halfY + radius + gap)));
}
