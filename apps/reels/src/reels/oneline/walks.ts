import type { Point } from "../../lib/ink";
import { cumulativeLengths, smoothPath } from "../../lib/plan";
import type { FloorPlan, ScanObject } from "../../lib/scan";

/** Moffitt was captured as four walks and stitched into one floor. The pose clock jumps at these indices, where one walk ends and the next begins. */
const MOFFITT_WALK_STARTS = [0, 942, 1843, 3110];
/** Minutes of walking across the four captures, summed from the pose timestamps with the gaps between walks left out. */
export const MOFFITT_WALK_MINUTES = 33;

/** A step longer than this is the phone relocalising, not someone walking, so the pen lifts across it. */
const JUMP_METRES = 2.5;

function splitAtJumps(points: Point[]): Point[][] {
  const pieces: Point[][] = [[]];
  points.forEach((point, index) => {
    const previous = points[index - 1];
    if (previous && Math.hypot(point[0] - previous[0], point[1] - previous[1]) > JUMP_METRES) pieces.push([]);
    pieces[pieces.length - 1].push(point);
  });
  return pieces.filter((piece) => piece.length > 8);
}

export type Walk = { strokes: Point[][]; metres: number };

function walkFrom(points: Point[]): Walk {
  const strokes = splitAtJumps(points).map((piece) => smoothPath(piece, 6));
  const metres = strokes.reduce((sum, stroke) => sum + cumulativeLengths(stroke).at(-1)!, 0);
  return { strokes, metres };
}

/** The walked path cut back into the separate walks it was captured as, each smoothed, with jumps between them removed. */
export function splitWalks(path: Point[]): Walk[] {
  return MOFFITT_WALK_STARTS.map((start, index) => walkFrom(path.slice(start, MOFFITT_WALK_STARTS[index + 1] ?? path.length)));
}

export const walkedMetres = (walks: Walk[]) => Math.round(walks.reduce((sum, walk) => sum + walk.metres, 0));

/** The angle, mod 90°, that most wall length runs along: the building's grid. */
function gridAngle(plan: FloorPlan) {
  const weights = new Map<number, number>();
  for (const [a, b] of plan.walls) {
    const degrees = Math.round(((Math.atan2(b[1] - a[1], b[0] - a[0]) * 180) / Math.PI + 360) % 90);
    weights.set(degrees, (weights.get(degrees) ?? 0) + Math.hypot(b[0] - a[0], b[1] - a[1]));
  }
  const [degrees] = [...weights.entries()].sort((p, q) => q[1] - p[1])[0];
  return (degrees * Math.PI) / 180;
}

function rotator(angle: number) {
  const [cos, sin] = [Math.cos(-angle), Math.sin(-angle)];
  return ([x, y]: Point): Point => [x * cos - y * sin, x * sin + y * cos];
}

function turnObject(object: ScanObject, turn: (point: Point) => Point): ScanObject {
  const [x, z] = turn([object.center[0], object.center[2]]);
  return { ...object, footprint: object.footprint.map(turn), center: [x, object.center[1], z] };
}

/** A further quarter turn when the squared plan would stand taller than it is wide, so it fills a landscape stage. */
function landscapeTurn(plan: FloorPlan, angle: number) {
  const turn = rotator(angle);
  const points = plan.walls.flat().map(turn);
  const span = (axis: 0 | 1) => Math.max(...points.map((point) => point[axis])) - Math.min(...points.map((point) => point[axis]));
  return span(1) > span(0) ? angle + Math.PI / 2 : angle;
}

/** Turns the plan so the building's walls run square to the sheet, which is how a drafted plan is drawn. Returns the turn so a camera can follow it. */
export function squarePlan(plan: FloorPlan): { plan: FloorPlan; angle: number } {
  const angle = landscapeTurn(plan, gridAngle(plan));
  const turn = rotator(angle);
  const turnSegment = ([a, b]: [Point, Point]): [Point, Point] => [turn(a), turn(b)];
  return {
    angle,
    plan: {
      ...plan,
      walls: plan.walls.map(turnSegment),
      windows: plan.windows.map(turnSegment),
      doors: plan.doors.map(turnSegment),
      objects: plan.objects.map((object) => turnObject(object, turn)),
      path: plan.path.map(turn),
    },
  };
}
