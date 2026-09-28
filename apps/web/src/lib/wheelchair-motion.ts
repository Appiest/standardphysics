import type { SceneNode } from "@/types/contracts";

export type MotionPoint = { x: number; z: number };

export type ReachProfile = {
  maxReachDistance?: number;
  minReachHeight?: number;
  maxReachHeight?: number;
  handReference?: string | null;
  distanceInterval?: [number, number];
  heightInterval?: [number, number];
};

/** Adjustable navigation estimates, not a model-specific wheelchair measurement. */
export type WheelchairProfile = {
  eyeHeight: number;
  speed: number;
  collisionRadius: number;
  reach?: ReachProfile | null;
};

export const WHEELCHAIR_PROFILE_LIMITS = {
  eyeHeight: { min: 0.75, max: 1.45 },
  speed: { min: 0.6, max: 4 },
  collisionRadius: { min: 0.3, max: 0.7 },
  minReachHeight: { min: 0.1, max: 0.8 },
  maxReachHeight: { min: 0.8, max: 1.8 },
  maxReachDistance: { min: 0.2, max: 1.2 },
} as const;

export const DEFAULT_WHEELCHAIR_PROFILE: WheelchairProfile = {
  eyeHeight: 1.15,
  speed: 2.4,
  collisionRadius: 0.45,
  reach: null,
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
    ...(profile?.reach !== undefined ? { reach: profile.reach ? { ...profile.reach } : null } : {}),
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

export function sceneRect(node: SceneNode): CollisionRect | null {
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

/** How far ahead the view names what it faces. Whether it is within reach is a separate question. */
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

type WheelchairPath = { path: MotionPoint[]; reached: boolean; distance: number | null };
type PathNode = { point: MotionPoint; g: number; f: number; parent: PathNode | null };

const PATH_STEP = 0.10;
const PATH_DIRECTIONS = [
  { x: PATH_STEP, z: 0, cost: PATH_STEP },
  { x: -PATH_STEP, z: 0, cost: PATH_STEP },
  { x: 0, z: PATH_STEP, cost: PATH_STEP },
  { x: 0, z: -PATH_STEP, cost: PATH_STEP },
  { x: PATH_STEP, z: PATH_STEP, cost: PATH_STEP * Math.SQRT2 },
  { x: PATH_STEP, z: -PATH_STEP, cost: PATH_STEP * Math.SQRT2 },
  { x: -PATH_STEP, z: PATH_STEP, cost: PATH_STEP * Math.SQRT2 },
  { x: -PATH_STEP, z: -PATH_STEP, cost: PATH_STEP * Math.SQRT2 },
];

function pathKey(point: MotionPoint): string {
  return `${Math.round(point.x / PATH_STEP)},${Math.round(point.z / PATH_STEP)}`;
}

function nearestOpenNode(openList: PathNode[]): PathNode {
  let bestIndex = 0;
  for (let index = 1; index < openList.length; index += 1) {
    if (openList[index].f < openList[bestIndex].f) bestIndex = index;
  }
  return openList.splice(bestIndex, 1)[0];
}

function pathFromNode(current: PathNode, goal: MotionPoint, geometry: WheelchairMotionGeometry, radius: number): WheelchairPath | null {
  if (length(subtract(goal, current.point)) > PATH_STEP * 2.0) return null;
  if (!sweepWheelchairInGeometry(current.point, subtract(goal, current.point), geometry, radius).reached) return null;
  const path: MotionPoint[] = [goal];
  let node: PathNode | null = current;
  while (node) {
    path.unshift(node.point);
    node = node.parent;
  }
  let distance = 0;
  for (let index = 0; index < path.length - 1; index += 1) {
    distance += length(subtract(path[index + 1], path[index]));
  }
  return { path, reached: true, distance };
}

function addPathNeighbors(
  current: PathNode,
  goal: MotionPoint,
  geometry: WheelchairMotionGeometry,
  radius: number,
  openList: PathNode[],
  closed: Set<string>,
  gScores: Map<string, number>,
): void {
  for (const direction of PATH_DIRECTIONS) {
    const point = add(current.point, direction);
    const key = pathKey(point);
    if (closed.has(key)) continue;
    if (!canOccupyWheelchair(point, geometry, radius)) continue;
    if (!sweepWheelchairInGeometry(current.point, subtract(point, current.point), geometry, radius).reached) continue;
    const g = current.g + direction.cost;
    const previous = gScores.get(key);
    if (previous !== undefined && g >= previous) continue;
    gScores.set(key, g);
    openList.push({ point, g, f: g + length(subtract(goal, point)), parent: current });
  }
}

function searchWheelchairPath(
  start: MotionPoint,
  goal: MotionPoint,
  geometry: WheelchairMotionGeometry,
  radius: number,
  maxExpansions: number,
): WheelchairPath {
  const openList: PathNode[] = [{ point: start, g: 0, f: length(subtract(goal, start)), parent: null }];
  const gScores = new Map([[pathKey(start), 0]]);
  const closed = new Set<string>();
  let expansions = 0;
  while (openList.length > 0 && expansions < maxExpansions) {
    const current = nearestOpenNode(openList);
    const key = pathKey(current.point);
    if (closed.has(key)) continue;
    closed.add(key);
    expansions += 1;
    const finished = pathFromNode(current, goal, geometry, radius);
    if (finished) return finished;
    addPathNeighbors(current, goal, geometry, radius, openList, closed, gScores);
  }
  return { path: [], reached: false, distance: null };
}

/** Finds a collision-free path for the wheelchair using direct sweep or A* search on measured floors. */
export function findWheelchairPath(
  start: MotionPoint,
  goal: MotionPoint,
  geometry: WheelchairMotionGeometry,
  radius: number,
  maxExpansions = 50000,
): WheelchairPath {
  if (sweepWheelchairInGeometry(start, subtract(goal, start), geometry, radius).reached) {
    return { path: [start, goal], reached: true, distance: length(subtract(goal, start)) };
  }
  return searchWheelchairPath(start, goal, geometry, radius, maxExpansions);
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

export type ApproachStatus = "clear" | "blocked" | "needs_verification";
export type ReachStatus = "within_reach" | "outside_reach" | "needs_verification";

export type OutletAccessibilityAssessment = {
  approachStatus: ApproachStatus;
  approachCandidate: MotionPoint | null;
  approachDistance: number | null;
  approachHeadingDeg: number | null;
  reachStatus: ReachStatus;
  targetHeightAboveFloor: number | null;
  reachDistance: number | null;
  unresolvedReasons: string[];
  disclaimers: string[];
};

type ApproachCandidate = { point: MotionPoint; distance: number; heading: number };
type ApproachSelection = {
  status: ApproachStatus;
  candidate: MotionPoint | null;
  distance: number | null;
  heading: number | null;
};
type ValidReachProfile = ReachProfile & {
  maxReachDistance: number;
  minReachHeight: number;
  maxReachHeight: number;
};

const OUTLET_DISCLAIMERS = [
  "Electrical power, circuit live status, voltage, and socket condition are not established.",
  "Plug insertion force, dexterity, and grip requirements are not established.",
  "ADA compliance is not established.",
  "Passing modeled reach is not proof of grasp, plug insertion, arm motion, strength or safe independent use.",
];

function outletHeightAboveFloor(
  target: MotionPoint,
  height: number,
  geometry: WheelchairMotionGeometry,
  reasons: string[],
): number | null {
  const floor = geometry.floors.find((candidate) => containsPoint(candidate, target, 0.05));
  if (floor) return Math.max(0, height - floor.node.transform.m[11]);
  reasons.push("No modeled floor under target; local floor elevation unknown.");
  return null;
}

function outletApproachCandidates(
  target: MotionPoint,
  normal: MotionPoint,
  current: MotionPoint | null | undefined,
  geometry: WheelchairMotionGeometry,
  radius: number,
): ApproachCandidate[] {
  const candidates: ApproachCandidate[] = [];
  const lateralAxis: MotionPoint = { x: -normal.z, z: normal.x };
  for (let distance = radius + 0.10; distance <= 0.70; distance += 0.10) {
    for (let lateral = -0.30; lateral <= 0.30; lateral += 0.10) {
      const point = add(target, add(scale(normal, distance), scale(lateralAxis, lateral)));
      if (!canOccupyWheelchair(point, geometry, radius)) continue;
      const toOutlet = subtract(target, point);
      const heading = (Math.atan2(toOutlet.x, toOutlet.z) * 180) / Math.PI;
      const startDistance = current ? length(subtract(current, point)) : 0;
      candidates.push({ point, distance: startDistance, heading });
    }
  }
  candidates.sort((left, right) => left.distance - right.distance);
  return candidates;
}

function outletReachObstructed(
  from: MotionPoint,
  target: MotionPoint,
  outletId: string,
  geometry: WheelchairMotionGeometry,
): boolean {
  return geometry.obstacles.some((obstacle) => {
    if (obstacle.node.id === outletId) return false;
    return segmentIntersectsRect(from, target, obstacle);
  });
}

function selectOutletApproach(
  current: MotionPoint | null | undefined,
  candidates: ApproachCandidate[],
  target: MotionPoint,
  outletId: string,
  geometry: WheelchairMotionGeometry,
  radius: number,
  reasons: string[],
): ApproachSelection {
  if (!current) return { status: "needs_verification", candidate: null, distance: null, heading: null };
  for (const candidate of candidates) {
    const path = findWheelchairPath(current, candidate.point, geometry, radius);
    if (!path.reached) continue;
    if (outletReachObstructed(candidate.point, target, outletId, geometry)) continue;
    return { status: "clear", candidate: candidate.point, distance: path.distance, heading: candidate.heading };
  }
  const nearest = candidates[0];
  if (nearest) {
    reasons.push("Candidate stopping position exists on floor, but route from current position is obstructed.");
    return { status: "blocked", candidate: nearest.point, distance: nearest.distance, heading: nearest.heading };
  }
  reasons.push("No valid modeled floor position found within approach range.");
  return { status: "blocked", candidate: null, distance: null, heading: null };
}

function validVerticalReach(reach: ReachProfile): boolean {
  return Number.isFinite(reach.minReachHeight)
    && Number.isFinite(reach.maxReachHeight)
    && reach.minReachHeight! >= 0
    && reach.minReachHeight! < reach.maxReachHeight!;
}

function validatedReach(profile: WheelchairProfile, reasons: string[]): ValidReachProfile | null {
  const reach = profile.reach;
  if (!reach) {
    reasons.push("Personalized reach profile not supplied; reach cannot be established.");
    return null;
  }
  if (reach.maxReachDistance === undefined) {
    reasons.push("Maximum reach distance not supplied; reach cannot be established.");
    return null;
  }
  if (reach.minReachHeight === undefined || reach.maxReachHeight === undefined) {
    reasons.push("Vertical reach limits (minReachHeight, maxReachHeight) not supplied; implicit ADA dimensions not assumed.");
    return null;
  }
  if (!Number.isFinite(reach.maxReachDistance) || reach.maxReachDistance <= 0) {
    reasons.push("Invalid reach profile: maxReachDistance must be a finite positive number.");
    return null;
  }
  if (!validVerticalReach(reach)) {
    reasons.push("Invalid reach profile: vertical limits must be finite numbers with minReachHeight < maxReachHeight.");
    return null;
  }
  return reach as ValidReachProfile;
}

function distanceReachStatus(distance: number, reach: ValidReachProfile, reasons: string[]): ReachStatus | null {
  const interval = reach.distanceInterval ?? [distance, distance];
  if (interval[0] <= reach.maxReachDistance && interval[1] > reach.maxReachDistance) {
    reasons.push("Reach distance interval crosses maximum reach threshold; needs in-person verification.");
    return "needs_verification";
  }
  if (interval[0] > reach.maxReachDistance) {
    reasons.push(`Estimated reach distance (${distance.toFixed(2)}m) exceeds maximum modeled reach (${reach.maxReachDistance.toFixed(2)}m).`);
    return "outside_reach";
  }
  return null;
}

function crossesVerticalReach(interval: [number, number], reach: ValidReachProfile): boolean {
  return (interval[0] < reach.minReachHeight && interval[1] >= reach.minReachHeight)
    || (interval[0] <= reach.maxReachHeight && interval[1] > reach.maxReachHeight);
}

function heightReachStatus(height: number, reach: ValidReachProfile, reasons: string[]): ReachStatus | null {
  const interval = reach.heightInterval ?? [height, height];
  if (crossesVerticalReach(interval, reach)) {
    reasons.push("Target height interval crosses vertical reach threshold; needs in-person verification.");
    return "needs_verification";
  }
  if (height < reach.minReachHeight || height > reach.maxReachHeight) {
    reasons.push(`Target height (${height.toFixed(2)}m) is outside vertical reach envelope (${reach.minReachHeight.toFixed(2)}m - ${reach.maxReachHeight.toFixed(2)}m).`);
    return "outside_reach";
  }
  return null;
}

function resolvedReachStatus(
  reach: ValidReachProfile,
  height: number,
  distance: number,
  from: MotionPoint | null | undefined,
  target: MotionPoint,
  outletId: string,
  geometry: WheelchairMotionGeometry,
  reasons: string[],
): ReachStatus {
  if (from && outletReachObstructed(from, target, outletId, geometry)) {
    reasons.push("Reach path from stopping position to outlet is obstructed by an intervening obstacle.");
    return "outside_reach";
  }
  return distanceReachStatus(distance, reach, reasons) ?? heightReachStatus(height, reach, reasons) ?? "within_reach";
}

function outletReachStatus(
  profile: WheelchairProfile,
  height: number | null,
  distance: number | null,
  from: MotionPoint | null | undefined,
  target: MotionPoint,
  outletId: string,
  geometry: WheelchairMotionGeometry,
  hasNormal: boolean,
  unresolvedSocket: boolean,
  reasons: string[],
): ReachStatus {
  const reach = validatedReach(profile, reasons);
  if (!reach) return "needs_verification";
  if (height === null) {
    reasons.push("Target height above floor is unknown; reach cannot be established.");
    return "needs_verification";
  }
  if (!hasNormal || unresolvedSocket || distance === null) return "needs_verification";
  return resolvedReachStatus(reach, height, distance, from, target, outletId, geometry, reasons);
}

function outletTargetContext(outlet: SceneNode, geometry: WheelchairMotionGeometry, reasons: string[]) {
  const position: MotionPoint = { x: outlet.transform.m[3], z: -outlet.transform.m[7] };
  const heightAboveFloor = outletHeightAboveFloor(position, outlet.transform.m[11], geometry, reasons);
  const rawNormal = outlet.attachment?.normal;
  if (!rawNormal) reasons.push("Outlet support normal is missing; orientation unknown.");
  const normal = rawNormal ? normalize({ x: rawNormal.x, z: -rawNormal.y }, { x: 0, z: 1 }) : { x: 0, z: 1 };
  const unresolvedSocket = Boolean(outlet.attachment?.uncertainty_reasons?.some((reason) => reason.toLowerCase().includes("unresolved socket")));
  if (unresolvedSocket) reasons.push("Socket target is unresolved; cannot confirm operable reach.");
  return { position, heightAboveFloor, normal, hasNormal: Boolean(rawNormal), unresolvedSocket };
}

function outletReachDistance(
  current: MotionPoint | null | undefined,
  approach: ApproachSelection,
  target: MotionPoint,
): number | null {
  const origin = approach.status === "clear" && approach.candidate ? approach.candidate : current;
  return origin ? length(subtract(origin, target)) : null;
}

export function assessOutletAccessibility(
  currentPos: MotionPoint | null | undefined,
  outletNode: SceneNode,
  geometry: WheelchairMotionGeometry,
  profile: WheelchairProfile,
): OutletAccessibilityAssessment {
  const unresolvedReasons: string[] = [];
  if (!currentPos) unresolvedReasons.push("Current wheelchair position is unknown; route cannot be evaluated.");

  const target = outletTargetContext(outletNode, geometry, unresolvedReasons);
  const candidates = outletApproachCandidates(target.position, target.normal, currentPos, geometry, profile.collisionRadius);
  const approach = selectOutletApproach(currentPos, candidates, target.position, outletNode.id, geometry, profile.collisionRadius, unresolvedReasons);
  const reachDistance = outletReachDistance(currentPos, approach, target.position);
  const reachFromPoint = approach.candidate ?? currentPos;
  const reachStatus = outletReachStatus(
    profile, target.heightAboveFloor, reachDistance, reachFromPoint, target.position, outletNode.id,
    geometry, target.hasNormal, target.unresolvedSocket, unresolvedReasons,
  );

  return {
    approachStatus: approach.status,
    approachCandidate: approach.candidate,
    approachDistance: approach.distance,
    approachHeadingDeg: approach.heading,
    reachStatus,
    targetHeightAboveFloor: target.heightAboveFloor,
    reachDistance,
    unresolvedReasons,
    disclaimers: [...OUTLET_DISCLAIMERS],
  };
}
