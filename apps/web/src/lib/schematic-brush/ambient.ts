import { clamp, polylineLength, type Box, type Point } from "./geometry";

export const AMBIENT_CELL_PX = 24;

const MINIMUM_CLEARANCE_CELLS = 3;
const STEP_PX = 6;
const TURN_TRIES = [0, 0.5, -0.5, 1, -1, 1.6, -1.6, 2.4, -2.4];
const MAX_ROUTES = 4;
const MINIMUM_ROUTE_PX = 180;
const ROUTE_BUDGET_PX = 900;
const ROUTE_SPACING_PX = 360;
const BOTTOM_BAND_CENTER = 0.84;
const BOTTOM_BAND_TOP = 0.55;

export interface FreeSpace {
  width: number;
  height: number;
  columns: number;
  rows: number;
  clearance: Uint16Array;
}

export interface AmbientPlanOptions {
  random?: () => number;
  budgetPx?: number;
}

function markBlocked(blocked: Uint8Array, columns: number, rows: number, box: Box) {
  const left = clamp(Math.floor(box[0] / AMBIENT_CELL_PX), 0, columns);
  const right = clamp(Math.ceil(box[2] / AMBIENT_CELL_PX), 0, columns);
  const top = clamp(Math.floor(box[1] / AMBIENT_CELL_PX), 0, rows);
  const bottom = clamp(Math.ceil(box[3] / AMBIENT_CELL_PX), 0, rows);
  for (let row = top; row < bottom; row++) blocked.fill(1, row * columns + left, row * columns + right);
}

function isEdge(index: number, columns: number, rows: number) {
  const column = index % columns;
  const row = Math.floor(index / columns);
  return column === 0 || row === 0 || column === columns - 1 || row === rows - 1;
}

const NEIGHBOUR_OFFSETS: readonly Point[] = [[-1, -1], [0, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [0, 1], [1, 1]];

function neighboursOf(index: number, columns: number, rows: number) {
  const column = index % columns;
  const row = Math.floor(index / columns);
  return NEIGHBOUR_OFFSETS.filter(([dx, dy]) => column + dx >= 0 && column + dx < columns && row + dy >= 0 && row + dy < rows).map(
    ([dx, dy]) => index + dy * columns + dx,
  );
}

function spreadClearance(clearance: Uint16Array, queue: number[], columns: number, rows: number) {
  for (let head = 0; head < queue.length; head++) {
    const index = queue[head];
    for (const neighbour of neighboursOf(index, columns, rows)) {
      if (clearance[neighbour] <= clearance[index] + 1) continue;
      clearance[neighbour] = clearance[index] + 1;
      queue.push(neighbour);
    }
  }
}

export function buildFreeSpace(width: number, height: number, keepOuts: readonly Box[]): FreeSpace {
  const columns = Math.max(1, Math.floor(width / AMBIENT_CELL_PX));
  const rows = Math.max(1, Math.floor(height / AMBIENT_CELL_PX));
  const blocked = new Uint8Array(columns * rows);
  for (const box of keepOuts) markBlocked(blocked, columns, rows, box);
  const clearance = new Uint16Array(columns * rows).fill(0xffff);
  const queue: number[] = [];
  blocked.forEach((isBlocked, index) => {
    if (!isBlocked && !isEdge(index, columns, rows)) return;
    clearance[index] = isBlocked ? 0 : 1;
    queue.push(index);
  });
  spreadClearance(clearance, queue, columns, rows);
  return { width, height, columns, rows, clearance };
}

export function clearanceAt(space: FreeSpace, x: number, y: number) {
  const column = Math.floor(x / AMBIENT_CELL_PX);
  const row = Math.floor(y / AMBIENT_CELL_PX);
  if (column < 0 || row < 0 || column >= space.columns || row >= space.rows) return 0;
  return space.clearance[row * space.columns + column];
}

function cellCenter(space: FreeSpace, index: number): Point {
  return [((index % space.columns) + 0.5) * AMBIENT_CELL_PX, (Math.floor(index / space.columns) + 0.5) * AMBIENT_CELL_PX];
}

function distanceFromDrawn(point: Point, drawn: readonly Point[]) {
  return drawn.reduce((nearest, other) => Math.min(nearest, Math.hypot(point[0] - other[0], point[1] - other[1])), Infinity);
}

function startWeight(space: FreeSpace, index: number, drawn: readonly Point[]) {
  const clearance = space.clearance[index];
  const depth = (Math.floor(index / space.columns) + 0.5) / space.rows;
  if (clearance < MINIMUM_CLEARANCE_CELLS + 1 || depth < BOTTOM_BAND_TOP) return 0;
  const spread = Math.min(1, distanceFromDrawn(cellCenter(space, index), drawn) / ROUTE_SPACING_PX) ** 2;
  return Math.min(clearance, 8) ** 2 * (0.02 + depth ** 6) * spread;
}

function pickStart(space: FreeSpace, drawn: readonly Point[], random: () => number): Point | null {
  const weights = Array.from(space.clearance, (_, index) => startWeight(space, index, drawn));
  const total = weights.reduce((sum, weight) => sum + weight, 0);
  if (total <= 0) return null;
  let roll = random() * total;
  const index = Math.max(0, weights.findIndex((weight) => (roll -= weight) < 0));
  return cellCenter(space, index);
}

function steerTowardBottom(space: FreeSpace, y: number, heading: number) {
  const targetY = space.height * BOTTOM_BAND_CENTER;
  return clamp((targetY - y) / space.height, -0.5, 0.5) * 0.4 * Math.sign(Math.cos(heading) || 1);
}

function nextStep(space: FreeSpace, x: number, y: number, heading: number): [number, number, number] | null {
  for (const turn of TURN_TRIES) {
    const angle = heading + turn;
    const nextX = x + Math.cos(angle) * STEP_PX;
    const nextY = y + Math.sin(angle) * STEP_PX;
    const inBand = nextY >= space.height * BOTTOM_BAND_TOP;
    if (inBand && clearanceAt(space, nextX, nextY) >= MINIMUM_CLEARANCE_CELLS) return [nextX, nextY, angle];
  }
  return null;
}

function wander(space: FreeSpace, start: Point, budgetPx: number, random: () => number): Point[] {
  const route: Point[] = [start];
  let [x, y] = start;
  let heading = random() < 0.5 ? 0 : Math.PI;
  for (let travelled = 0; travelled < budgetPx; travelled += STEP_PX) {
    heading += (random() - 0.5) * 0.25 + steerTowardBottom(space, y, heading);
    const step = nextStep(space, x, y, heading);
    if (!step) break;
    [x, y, heading] = step;
    route.push([x, y]);
  }
  return route;
}

export function ambientBudget(space: FreeSpace) {
  const openCells = space.clearance.reduce((count, clearance) => count + (clearance >= MINIMUM_CLEARANCE_CELLS ? 1 : 0), 0);
  return clamp(openCells * AMBIENT_CELL_PX * AMBIENT_CELL_PX * 0.0028, 0, 2600);
}

export function planAmbientRoutes(space: FreeSpace, options: AmbientPlanOptions = {}): Point[][] {
  const random = options.random ?? Math.random;
  let remaining = options.budgetPx ?? ambientBudget(space);
  const routes: Point[][] = [];
  const drawn: Point[] = [];
  for (let attempt = 0; attempt < MAX_ROUTES && remaining >= MINIMUM_ROUTE_PX; attempt++) {
    const start = pickStart(space, drawn, random);
    if (!start) break;
    const route = wander(space, start, Math.min(remaining, ROUTE_BUDGET_PX), random);
    const length = polylineLength(route);
    if (length < MINIMUM_ROUTE_PX) continue;
    routes.push(route);
    drawn.push(...route.filter((_, index) => index % 8 === 0));
    remaining -= length;
  }
  return routes;
}
