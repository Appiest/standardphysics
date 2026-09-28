import type { FloorPlan, ScanObject } from "./scan";
import type { Point } from "./ink";

export type SheetFrame = { scale: number; offsetX: number; offsetY: number };

/** Fits the plan's walls and walked path into a box on the sheet, keeping metres square. */
export function fitPlan(plan: FloorPlan, box: { x: number; y: number; width: number; height: number }): SheetFrame {
  const points = [...plan.walls.flat(), ...plan.path];
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const scale = Math.min(box.width / (maxX - minX), box.height / (maxY - minY));
  return {
    scale,
    offsetX: box.x + (box.width - (maxX - minX) * scale) / 2 - minX * scale,
    offsetY: box.y + (box.height - (maxY - minY) * scale) / 2 - minY * scale,
  };
}

export const onSheet = (frame: SheetFrame, [x, y]: Point): Point => [frame.offsetX + x * frame.scale, frame.offsetY + y * frame.scale];

/** The phone's raw track jitters with every step, so it is averaged over a window before a pen follows it. */
export function smoothPath(points: readonly Point[], window = 7): Point[] {
  return points.map((_, index) => {
    const from = Math.max(0, index - window);
    const to = Math.min(points.length, index + window + 1);
    const slice = points.slice(from, to);
    return [slice.reduce((sum, p) => sum + p[0], 0) / slice.length, slice.reduce((sum, p) => sum + p[1], 0) / slice.length] as const;
  });
}

/** Where along the path (0 to 1, by distance walked) the walker came closest to a point. */
export function closestShare(path: readonly Point[], target: Point) {
  const lengths = cumulativeLengths(path);
  let best = 0;
  let bestDistance = Infinity;
  path.forEach((point, index) => {
    const distance = Math.hypot(point[0] - target[0], point[1] - target[1]);
    if (distance < bestDistance) [best, bestDistance] = [index, distance];
  });
  return lengths[best] / lengths[lengths.length - 1];
}

export function cumulativeLengths(path: readonly Point[]) {
  const lengths = [0];
  for (let index = 1; index < path.length; index++) lengths.push(lengths[index - 1] + Math.hypot(path[index][0] - path[index - 1][0], path[index][1] - path[index - 1][1]));
  return lengths;
}

export function pointAtShare(path: readonly Point[], share: number): Point {
  const lengths = cumulativeLengths(path);
  const target = Math.min(1, Math.max(0, share)) * lengths[lengths.length - 1];
  const index = Math.max(1, lengths.findIndex((length) => length >= target));
  const span = lengths[index] - lengths[index - 1] || 1;
  const t = (target - lengths[index - 1]) / span;
  const [a, b] = [path[index - 1], path[index]];
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
}

export function centroid(points: readonly Point[]): Point {
  return [points.reduce((sum, p) => sum + p[0], 0) / points.length, points.reduce((sum, p) => sum + p[1], 0) / points.length];
}

/** An object's footprint as a centre, width, depth and turn, which is what a CSS box needs to stand it up. */
export function boxOf(object: ScanObject) {
  const [a, b, , d] = object.footprint;
  const center = centroid(object.footprint);
  return {
    center,
    width: Math.hypot(b[0] - a[0], b[1] - a[1]),
    depth: Math.hypot(d[0] - a[0], d[1] - a[1]),
    angle: Math.atan2(b[1] - a[1], b[0] - a[0]),
    height: object.size[1],
  };
}

export const inches = (metres: number) => `${Math.round(metres * 39.37)}"`;
