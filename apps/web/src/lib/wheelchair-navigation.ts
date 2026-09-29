import { EPSILON, add, dot, length, scale, subtract, type MotionPoint } from "./motion-vector";
import { collidesAt, containsPoint, type CollisionRect, type WheelchairMotionGeometry } from "./wheelchair-geometry";

export type SweptMove = {
  point: MotionPoint;
  reached: boolean;
};

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
