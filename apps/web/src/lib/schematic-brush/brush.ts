import { decorate, labelNode } from "./annotations";
import { boundsOf, boxesOverlap, type Box, type Point } from "./geometry";
import { INK, markDuration, specksAround, type Mark, type Stamp } from "./marks";
import { createNode, drawNode, type DraftNode } from "./nodes";
import { chance } from "./random";
import { DraftRecorder, Occupancy, type MeasureText } from "./recorder";
import { connect } from "./wiring";

const NODE_SPACING_UNITS = 6;
const MINIMUM_NODE_GAP_UNITS = 2.5;
const FOLLOWER_SUBSTEPS = 4;
const PEN_OVERLAP = 0.8;

interface StrokeState {
  x: number;
  y: number;
  targetX: number;
  targetY: number;
  pressed: boolean;
  travel: number;
  nodes: DraftNode[];
}

export interface SchematicBrushOptions {
  unit: number;
  measureText: MeasureText;
  now: () => number;
  speed?: number;
}

export class SchematicBrush {
  private stroke: StrokeState | null = null;
  private stamps: Stamp[] = [];
  private scheduleOffset = 0;
  private inkedAreas: Box[] = [];
  private readonly occupancy = new Occupancy();

  constructor(private readonly options: SchematicBrushOptions) {}

  get activeStamps(): readonly Stamp[] {
    return this.stamps;
  }

  pointerDown(x: number, y: number) {
    this.stroke = { x, y, targetX: x, targetY: y, pressed: true, travel: 0, nodes: [] };
  }

  pointerMove(x: number, y: number) {
    if (!this.stroke) return;
    this.stroke.targetX = x;
    this.stroke.targetY = y;
  }

  pointerUp() {
    if (this.stroke) this.stroke.pressed = false;
  }

  tick(): Stamp[] {
    this.advanceStroke();
    const now = this.options.now();
    const finished = this.stamps.filter((stamp) => now >= stamp.endsAt);
    this.stamps = this.stamps.filter((stamp) => now < stamp.endsAt);
    return finished;
  }

  takeAll(): Stamp[] {
    this.advanceStroke();
    const all = this.stamps;
    this.stamps = [];
    return all;
  }

  clear() {
    this.stroke = null;
    this.stamps = [];
    this.inkedAreas = [];
    this.occupancy.clear();
  }

  setKeepOuts(keepOuts: Box[], bounds: Box | null) {
    this.occupancy.setKeepOuts(keepOuts, bounds);
  }

  inkTouches(boxes: readonly Box[]) {
    return this.inkedAreas.some((inked) => boxes.some((box) => boxesOverlap(inked, box)));
  }

  draftRoute(route: readonly Point[], durationMs: number, startDelayMs = 0) {
    if (route.length < 2) return;
    this.pointerDown(route[0][0], route[0][1]);
    route.forEach(([x, y], index) => {
      this.scheduleOffset = startDelayMs + (index / (route.length - 1)) * durationMs;
      this.strokeTo(x, y);
    });
    this.endStroke();
    this.scheduleOffset = 0;
  }

  stampWith(draw: (recorder: DraftRecorder) => void, startDelayMs = 0) {
    const recorder = new DraftRecorder(this.options.unit, this.occupancy, this.options.measureText);
    draw(recorder);
    this.inkedAreas.push(...recorder.marks.map((mark) => recorder.boundsOfMark(mark)));
    this.scheduleOffset = startDelayMs;
    this.commit(recorder.marks);
    this.scheduleOffset = 0;
  }

  private advanceStroke() {
    const stroke = this.stroke;
    if (!stroke) return;
    for (let substep = 0; substep < FOLLOWER_SUBSTEPS; substep++) {
      const dx = stroke.targetX - stroke.x;
      const dy = stroke.targetY - stroke.y;
      const gap = Math.hypot(dx, dy);
      if (gap < 0.4) break;
      const step = Math.min(gap, Math.max(1.5, gap * 0.15));
      this.strokeTo(stroke.x + (dx / gap) * step, stroke.y + (dy / gap) * step);
    }
    if (!stroke.pressed && Math.hypot(stroke.targetX - stroke.x, stroke.targetY - stroke.y) < 0.8) this.endStroke();
  }

  private strokeTo(x: number, y: number) {
    const stroke = this.stroke;
    if (!stroke) return;
    const travelled = Math.hypot(x - stroke.x, y - stroke.y);
    if (travelled < 0.3) return;
    stroke.travel += travelled;
    stroke.x = x;
    stroke.y = y;
    if (stroke.travel < this.options.unit * NODE_SPACING_UNITS) return;
    stroke.travel = 0;
    this.placeNode(stroke, x, y);
  }

  private endStroke() {
    const stroke = this.stroke;
    if (stroke && stroke.nodes.length === 0) this.placeNode(stroke, stroke.x, stroke.y);
    this.stroke = null;
  }

  private placeNode(stroke: StrokeState, rawX: number, rawY: number) {
    const { unit, measureText } = this.options;
    const recorder = new DraftRecorder(unit, this.occupancy, measureText);
    const x = recorder.snap(rawX);
    const y = recorder.snap(rawY);
    const previous = stroke.nodes.at(-1) ?? null;
    if (previous && Math.hypot(previous.x - x, previous.y - y) < unit * MINIMUM_NODE_GAP_UNITS) return;
    const node = createNode(x, y, unit);
    const placed = recorder.attempt(() => {
      this.occupancy.claim([x - node.rx, y - node.ry, x + node.rx, y + node.ry]);
      if (previous) connect(recorder, previous, node);
      drawNode(recorder, node);
      if (chance(0.85)) labelNode(recorder, node);
    });
    if (!placed) return;
    decorate(recorder, node, previous);
    stroke.nodes.push(node);
    this.inkedAreas.push(...recorder.marks.map((mark) => recorder.boundsOfMark(mark)));
    this.commit(recorder.marks);
  }

  private specksFor(marks: Mark[]) {
    const firstInk = marks.find((mark) => mark.kind === "ink");
    if (!firstInk || !chance(INK.speckChance)) return null;
    const specks = specksAround(firstInk.runs[0].points[0], this.options.unit);
    specks.specks = specks.specks.filter((speck) => this.occupancy.isAllowed(boundsOf([[speck.x, speck.y]])));
    return specks;
  }

  private commit(marks: Mark[]) {
    if (marks.length === 0) return;
    const specks = this.specksFor(marks);
    if (specks) marks.push(specks);
    const speed = this.options.speed ?? 1;
    let penTime = 0;
    let endsAt = 0;
    for (const mark of marks) {
      mark.duration = markDuration(mark, speed);
      mark.start = penTime;
      penTime += mark.duration * PEN_OVERLAP;
      endsAt = Math.max(endsAt, mark.start + mark.duration);
    }
    const startsAt = this.options.now() + this.scheduleOffset;
    this.stamps.push({ marks, startsAt, endsAt: startsAt + endsAt });
  }
}
