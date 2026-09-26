import { resample } from "../../../../web/src/lib/schematic-brush/geometry";
import type { Point } from "../../lib/ink";
import { cumulativeLengths, fitPlan, onSheet, pointAtShare, type SheetFrame } from "../../lib/plan";
import type { FloorPlan } from "../../lib/scan";
import { splitWalks, squarePlan, walkedMetres, type Walk } from "./walks";

/** The schematic brush draws a line at this many pixels per millisecond before its speed factor is applied. */
const BRUSH_PX_PER_MS = 0.7;
const PEN_SPACING_PX = 3;

/** One pen-down stretch of a walk, in sheet pixels, with when the pen starts it and how long it takes. */
export type TimedStroke = { points: Point[]; lengths: number[]; startMs: number; durationMs: number };

export type WalkSheet = {
  plan: FloorPlan;
  /** How far the plan was turned to square it, so a 3D camera can be turned to match. */
  angle: number;
  frame: SheetFrame;
  /** One list of strokes per walk; every walk's pen starts at the same moment. */
  pens: TimedStroke[][];
  /** Brush speed factor that makes the longest walk finish in the walk's time. */
  speed: number;
  metres: number;
  /** A stretch of real walked path among the furniture, in plan metres, for the turning circle. */
  aisle: Point[];
};

function timeStrokes(strokes: Point[][], speed: number, inkZoom: number): TimedStroke[] {
  let startMs = 0;
  return strokes.map((points) => {
    const lengths = cumulativeLengths(points);
    const durationMs = lengths.at(-1)! / inkZoom / (BRUSH_PX_PER_MS * speed);
    const stroke = { points, lengths, startMs, durationMs };
    startMs += durationMs;
    return stroke;
  });
}

function sheetStrokes(walk: Walk, frame: SheetFrame) {
  return walk.strokes.map((stroke) => resample(stroke.map((point) => onSheet(frame, point)), PEN_SPACING_PX));
}

const inkLength = (strokes: Point[][]) => strokes.reduce((sum, stroke) => sum + cumulativeLengths(stroke).at(-1)!, 0);

/** Counts the furniture within reach of each stretch of path and keeps the stretch that threads the most of it. */
function busiestAisle(plan: FloorPlan, walks: Walk[], stretchMetres = 11): Point[] {
  const centers = plan.objects.map((object): Point => [object.center[0], object.center[2]]);
  const nearby = (point: Point) => centers.filter((center) => Math.hypot(center[0] - point[0], center[1] - point[1]) < 1.6).length;
  let best: { score: number; aisle: Point[] } = { score: -1, aisle: [] };
  for (const stroke of walks.flatMap((walk) => walk.strokes)) {
    const lengths = cumulativeLengths(stroke);
    for (let start = 0; start < stroke.length; start += 10) {
      const end = lengths.findIndex((length) => length > lengths[start] + stretchMetres);
      if (end < 0) break;
      const aisle = stroke.slice(start, end);
      const score = aisle.filter((_, index) => index % 4 === 0).reduce((sum, point) => sum + nearby(point), 0);
      if (score > best.score) best = { score, aisle };
    }
  }
  return best.aisle;
}

type SheetBox = { x: number; y: number; width: number; height: number };

/**
 * Everything both one-line reels need from the Moffitt floor: a squared plan, the four walks timed as pens, and an aisle.
 * The brush times lines in its own ink space, which is the sheet shrunk by inkZoom, so the pens are timed in that space too.
 */
export function buildWalkSheet(rawPlan: FloorPlan, box: SheetBox, walkMs: number, inkZoom: number): WalkSheet {
  const { plan, angle } = squarePlan(rawPlan);
  const walks = splitWalks(plan.path);
  const frame = fitPlan(plan, box);
  const strokesByWalk = walks.map((walk) => sheetStrokes(walk, frame));
  const speed = Math.max(...strokesByWalk.map(inkLength)) / inkZoom / (BRUSH_PX_PER_MS * walkMs);
  return {
    plan,
    angle,
    frame,
    speed,
    pens: strokesByWalk.map((strokes) => timeStrokes(strokes, speed, inkZoom)),
    metres: walkedMetres(walks),
    aisle: busiestAisle(plan, walks),
  };
}

/** Where a walk's pen is at a moment, or null once that walk is finished. */
export function penAt(strokes: TimedStroke[], timeMs: number): Point | null {
  const stroke = strokes.find((candidate) => timeMs < candidate.startMs + candidate.durationMs);
  if (!stroke) return null;
  return pointAtShare(stroke.points, Math.max(0, (timeMs - stroke.startMs) / stroke.durationMs));
}

/** When the nearest pen passes closest to a point on the sheet. */
export function whenPassed(pens: TimedStroke[][], target: Point) {
  let best = { distance: Infinity, ms: 0 };
  for (const stroke of pens.flat()) {
    stroke.points.forEach((point, index) => {
      const distance = Math.hypot(point[0] - target[0], point[1] - target[1]);
      if (distance < best.distance) best = { distance, ms: stroke.startMs + (stroke.lengths[index] / stroke.lengths.at(-1)!) * stroke.durationMs };
    });
  }
  return best.ms;
}
