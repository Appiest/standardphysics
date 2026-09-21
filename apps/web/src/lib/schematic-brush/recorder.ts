import {
  arcPoints,
  boundsOf,
  boxesOverlap,
  boxInside,
  inflateBox,
  roundedRectPoints,
  splitDashes,
  TAU,
  type Box,
  type Point,
} from "./geometry";
import {
  createRun,
  INK,
  LABEL_SCALE,
  LINE_WEIGHT,
  type InkMark,
  type LineWeight,
  type Mark,
  type TextAlign,
  type TextBaseline,
} from "./marks";
import { random } from "./random";

export type MeasureText = (text: string, size: number) => number;
export type Dash = readonly [number, number];

export const ALIGN_OFFSET: Record<TextAlign, number> = { left: 0, center: -0.5, right: -1 };
const BASELINE_OFFSET: Record<TextBaseline, number> = { top: 0, middle: -0.5, bottom: -1 };

export interface TextPlacement {
  align?: TextAlign;
  baseline?: TextBaseline;
  rotation?: number;
}

interface CircleOptions {
  dash?: Dash;
  fill?: boolean;
  from?: number;
  to?: number;
}

export class Occupancy {
  private claims: Box[] = [];
  private keepOuts: Box[] = [];
  private bounds: Box | null = null;

  setKeepOuts(keepOuts: Box[], bounds: Box | null) {
    this.keepOuts = keepOuts;
    this.bounds = bounds;
  }

  isAllowed(box: Box) {
    if (this.bounds && !boxInside(box, this.bounds)) return false;
    return this.keepOuts.every((keepOut) => !boxesOverlap(box, keepOut));
  }

  isFree(box: Box) {
    return this.isAllowed(box) && this.claims.every((taken) => !boxesOverlap(box, taken));
  }

  claim(box: Box) {
    this.claims.push(box);
  }

  get claimCount() {
    return this.claims.length;
  }

  rollbackTo(claimCount: number) {
    this.claims.length = claimCount;
  }

  clear() {
    this.claims = [];
  }
}

export class DraftRecorder {
  readonly marks: Mark[] = [];

  constructor(
    readonly unit: number,
    readonly occupancy: Occupancy,
    private readonly measureText: MeasureText,
  ) {}

  snap(value: number) {
    const step = this.unit * 0.5;
    return Math.round(value / step) * step;
  }

  fontSize(multiplier = 0.72) {
    return Math.max(6.5, this.unit * multiplier * 0.92) * LABEL_SCALE;
  }

  measure(text: string, size: number) {
    return this.measureText(text, size);
  }

  line(points: readonly Point[], weight: LineWeight, dash?: Dash, fill = false) {
    if (points.length < 2) return;
    const pieces = dash ? splitDashes(points, dash[0], dash[1]) : [points];
    const runs = pieces.map((piece) => createRun(piece, this.unit));
    const length = runs.reduce((sum, run) => sum + run.length, 0);
    if (length <= 0) return;
    this.marks.push({
      kind: "ink",
      runs,
      weight: LINE_WEIGHT[weight],
      length,
      fillPolygon: fill ? [...points] : null,
      start: 0,
      duration: 0,
    });
  }

  circle(cx: number, cy: number, radius: number, weight: LineWeight, options: CircleOptions = {}) {
    const { dash, fill = false, from = 0, to = TAU } = options;
    this.line(arcPoints(cx, cy, radius, from, to), weight, dash, fill);
  }

  roundedRect(x: number, y: number, width: number, height: number, radius: number, weight: LineWeight, fill = false) {
    this.line(roundedRectPoints(x, y, width, height, radius), weight, undefined, fill);
  }

  textAt(text: string, x: number, y: number, size: number, placement: TextPlacement = {}) {
    const { align = "left", baseline = "middle", rotation = 0 } = placement;
    this.marks.push({ kind: "text", text, x, y, size, align, baseline, rotation, start: 0, duration: 0 });
  }

  textClear(text: string, x: number, y: number, size: number, placement: TextPlacement = {}): Point | null {
    const g = this.unit;
    const nudges: Point[] = [[0, 0], [0, -g], [0, g], [g, 0], [-g, 0], [0, -2 * g], [0, 2 * g], [g, -g], [g, g], [-g, -g], [-g, g]];
    for (const [dx, dy] of nudges) {
      const box = this.textBox(text, x + dx, y + dy, size, placement);
      if (!this.occupancy.isFree(box)) continue;
      this.occupancy.claim(box);
      this.textAt(text, x + dx, y + dy, size, placement);
      return [x + dx, y + dy];
    }
    return null;
  }

  textBox(text: string, x: number, y: number, size: number, placement: TextPlacement = {}): Box {
    const { align = "left", baseline = "middle", rotation = 0 } = placement;
    const width = this.measure(text, size);
    const left = ALIGN_OFFSET[align] * width;
    const top = BASELINE_OFFSET[baseline] * size;
    const cos = Math.cos(rotation);
    const sin = Math.sin(rotation);
    const corners: Point[] = [[left, top], [left + width, top], [left, top + size], [left + width, top + size]].map(
      ([px, py]) => [x + px * cos - py * sin, y + px * sin + py * cos] as const,
    );
    const pad = this.unit * 0.22;
    const xs = corners.map((corner) => corner[0]);
    const ys = corners.map((corner) => corner[1]);
    return [Math.min(...xs) - pad, Math.min(...ys) - pad, Math.max(...xs) + pad, Math.max(...ys) + pad];
  }

  attempt(draw: () => void): boolean {
    const markCount = this.marks.length;
    const claimCount = this.occupancy.claimCount;
    draw();
    const added = this.marks.slice(markCount);
    if (added.every((mark) => this.occupancy.isAllowed(this.boundsOfMark(mark)))) return true;
    this.marks.length = markCount;
    this.occupancy.rollbackTo(claimCount);
    return false;
  }

  boundsOfMark(mark: Mark): Box {
    switch (mark.kind) {
      case "ink":
        return inkBounds(mark);
      case "text":
        return this.textBox(mark.text, mark.x, mark.y, mark.size, mark);
      case "speck":
        return boundsOf(mark.specks.map((speck) => [speck.x, speck.y] as const));
      default:
        return inflateBox(boundsOf(mark.points), 2);
    }
  }

  blot(outline: readonly Point[]) {
    const cx = outline.reduce((sum, point) => sum + point[0], 0) / outline.length;
    const cy = outline.reduce((sum, point) => sum + point[1], 0) / outline.length;
    const points = outline.map(([x, y]) => {
      const dx = x - cx;
      const dy = y - cy;
      const length = Math.hypot(dx, dy) || 1;
      const offset = random(-1, 1) * INK.raggedness;
      return [x + (dx / length) * offset, y + (dy / length) * offset] as const;
    });
    this.marks.push({ kind: "blot", points, cx, cy, start: 0, duration: 0 });
  }

  dot(x: number, y: number, radius: number) {
    this.blot(Array.from({ length: 14 }, (_, i) => [x + Math.cos((i / 14) * TAU) * radius, y + Math.sin((i / 14) * TAU) * radius] as const));
  }
}

function inkBounds(mark: InkMark): Box {
  const [x0, y0, x1, y1] = inflateBox(boundsOf(mark.runs.flatMap((run) => run.points)), mark.weight * (INK.bleedReach * 1.6 + 3));
  const longestDrip = Math.max(0, ...mark.runs.flatMap((run) => run.blobs.map((blob) => blob.drip)));
  return [x0, y0, x1, y1 + longestDrip];
}
