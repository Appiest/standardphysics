export type Point = readonly [number, number];
export type Box = readonly [number, number, number, number];

export const HALF_PI = Math.PI / 2;
export const QUARTER_PI = Math.PI / 4;
export const TAU = Math.PI * 2;

export function lerp(from: number, to: number, t: number) {
  return from + (to - from) * t;
}

export function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export function distance(a: Point, b: Point) {
  return Math.hypot(b[0] - a[0], b[1] - a[1]);
}

export function pointBetween(a: Point, b: Point, t: number): Point {
  return [lerp(a[0], b[0], t), lerp(a[1], b[1], t)];
}

export function polylineLength(points: readonly Point[]) {
  let length = 0;
  for (let i = 1; i < points.length; i++) length += distance(points[i - 1], points[i]);
  return length;
}

export function cutPolyline(points: readonly Point[], budget: number): Point[] {
  if (budget <= 0 || points.length === 0) return [];
  const cut: Point[] = [points[0]];
  let remaining = budget;
  for (let i = 1; i < points.length; i++) {
    const segment = distance(points[i - 1], points[i]);
    if (remaining < segment) {
      cut.push(pointBetween(points[i - 1], points[i], remaining / segment));
      return cut;
    }
    cut.push(points[i]);
    remaining -= segment;
  }
  return cut;
}

export function resample(points: readonly Point[], spacing: number): Point[] {
  if (points.length < 2) return [...points];
  const dense: Point[] = [points[0]];
  for (let i = 1; i < points.length; i++) {
    const steps = Math.max(1, Math.ceil(distance(points[i - 1], points[i]) / spacing));
    for (let k = 1; k <= steps; k++) dense.push(pointBetween(points[i - 1], points[i], k / steps));
  }
  return dense;
}

class DashPattern {
  readonly dashes: Point[][] = [];
  private current: Point[];
  private drawing = true;
  remaining: number;

  constructor(start: Point, private readonly dashLength: number, private readonly gapLength: number) {
    this.current = [start];
    this.remaining = dashLength;
  }

  advance(point: Point, step: number) {
    this.remaining -= step;
    if (this.drawing) this.current.push(point);
    if (this.remaining > 0) return;
    if (this.drawing) this.dashes.push(this.current);
    this.current = this.drawing ? [] : [point];
    this.drawing = !this.drawing;
    this.remaining = this.drawing ? this.dashLength : this.gapLength;
  }

  finish() {
    if (this.current.length > 1) this.dashes.push(this.current);
    return this.dashes;
  }
}

export function splitDashes(points: readonly Point[], dashLength: number, gapLength: number): Point[][] {
  const pattern = new DashPattern(points[0], dashLength, gapLength);
  for (let i = 1; i < points.length; i++) {
    const segment = distance(points[i - 1], points[i]);
    let travelled = 0;
    while (segment - travelled > 1e-9) {
      const step = Math.min(pattern.remaining, segment - travelled);
      travelled += step;
      pattern.advance(pointBetween(points[i - 1], points[i], travelled / segment), step);
    }
  }
  return pattern.finish();
}

export function arcPoints(cx: number, cy: number, radius: number, from: number, to: number): Point[] {
  const count = Math.max(12, Math.floor((radius * Math.abs(to - from)) / 3));
  return Array.from({ length: count + 1 }, (_, i) => {
    const angle = lerp(from, to, i / count);
    return [cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius] as const;
  });
}

function cubic(a: number, b: number, c: number, d: number, t: number) {
  const inverse = 1 - t;
  return inverse ** 3 * a + 3 * inverse ** 2 * t * b + 3 * inverse * t ** 2 * c + t ** 3 * d;
}

export function bezierPoints(start: Point, control1: Point, control2: Point, end: Point, count = 40): Point[] {
  return Array.from({ length: count + 1 }, (_, i) => {
    const t = i / count;
    return [cubic(start[0], control1[0], control2[0], end[0], t), cubic(start[1], control1[1], control2[1], end[1], t)] as const;
  });
}

export function roundedRectPoints(x: number, y: number, width: number, height: number, radius: number): Point[] {
  const r = Math.min(radius, width / 2, height / 2);
  const corners: [number, number, number][] = [
    [x + width - r, y + r, -HALF_PI],
    [x + width - r, y + height - r, 0],
    [x + r, y + height - r, HALF_PI],
    [x + r, y + r, Math.PI],
  ];
  const points: Point[] = corners.flatMap(([cx, cy, start]) =>
    Array.from({ length: 7 }, (_, i) => {
      const angle = start + (HALF_PI * i) / 6;
      return [cx + Math.cos(angle) * r, cy + Math.sin(angle) * r] as const;
    }),
  );
  return [...points, points[0]];
}

export function rectPoints(x: number, y: number, width: number, height: number): Point[] {
  return [
    [x, y],
    [x + width, y],
    [x + width, y + height],
    [x, y + height],
    [x, y],
  ];
}

export function boundsOf(points: readonly Point[]): Box {
  const xs = points.map((point) => point[0]);
  const ys = points.map((point) => point[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

export function boxesOverlap(a: Box, b: Box) {
  return a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1];
}

export function boxInside(inner: Box, outer: Box) {
  return inner[0] >= outer[0] && inner[1] >= outer[1] && inner[2] <= outer[2] && inner[3] <= outer[3];
}

export function inflateBox(box: Box, amount: number): Box {
  return [box[0] - amount, box[1] - amount, box[2] + amount, box[3] + amount];
}

export function boxAround(x: number, y: number, halfWidth: number, halfHeight = halfWidth): Box {
  return [x - halfWidth, y - halfHeight, x + halfWidth, y + halfHeight];
}
