import { dimensionAbove, dimensionLeft } from "../../../../web/src/lib/schematic-brush/annotations";
import type { SchematicBrush } from "../../../../web/src/lib/schematic-brush/brush";
import { boundsOf } from "../../../../web/src/lib/schematic-brush/geometry";
import type { DraftRecorder } from "../../../../web/src/lib/schematic-brush/recorder";
import { composeInk, type Point, type Stamp } from "../../lib/ink";
import { inches, onSheet } from "../../lib/plan";
import type { ScanObject } from "../../lib/scan";
import { whenPassed, type WalkSheet } from "./walkSheet";

/** Only the widest tables get their measured width written on the sheet; labelling all fifty would bury the plan. */
const LABELLED_TABLES = 4;

type Drafting = { sheet: WalkSheet; inkZoom: number };

const toInk = ({ inkZoom }: Drafting, [x, y]: Point): Point => [x / inkZoom, y / inkZoom];

function dimensionAlongWidth(d: DraftRecorder, corners: Point[], label: string) {
  const [x0, y0, x1, y1] = boundsOf(corners);
  const [a, b] = corners;
  if (Math.abs(b[0] - a[0]) >= Math.abs(b[1] - a[1])) dimensionAbove(d, [x0, y0], [x1, y0], y0 - d.unit * 1.2, label);
  else dimensionLeft(d, [x0, y0], [x0, y1], x0 - d.unit * 1.2, label);
}

export function objectCorners(drafting: Drafting, object: ScanObject) {
  return object.footprint.map((corner) => toInk(drafting, onSheet(drafting.sheet.frame, corner)));
}

function drawObject(d: DraftRecorder, drafting: Drafting, object: ScanObject, labelled: boolean) {
  const corners = objectCorners(drafting, object);
  d.line([...corners, corners[0]], object.name === "table" ? "regular" : "thin");
  if (labelled) dimensionAlongWidth(d, corners, inches(object.size[0]));
}

function widestTables(objects: ScanObject[]) {
  return new Set(
    objects
      .filter((object) => object.name === "table")
      .sort((a, b) => b.size[0] - a.size[0])
      .slice(0, LABELLED_TABLES),
  );
}

export function passedAt({ sheet }: Drafting, point: Point) {
  return whenPassed(sheet.pens, onSheet(sheet.frame, point));
}

function draftRoom(brush: SchematicBrush, drafting: Drafting) {
  const { plan, frame, pens } = drafting.sheet;
  pens.forEach((strokes) => strokes.forEach((stroke) => brush.stampWith((d) => d.line(stroke.points.map((point) => toInk(drafting, point)), "regular"), stroke.startMs)));
  plan.walls.forEach(([a, b]) =>
    brush.stampWith((d) => d.line([toInk(drafting, onSheet(frame, a)), toInk(drafting, onSheet(frame, b))], "heavy"), passedAt(drafting, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2])),
  );
  const labelled = widestTables(plan.objects);
  plan.objects.forEach((object) => brush.stampWith((d) => drawObject(d, drafting, object, labelled.has(object)), passedAt(drafting, [object.center[0], object.center[2]])));
}

/** The brush flicks specks around every stamp; across a whole library floor they add up to grime, so they are left off. */
export function withoutSpecks(stamps: Stamp[]): Stamp[] {
  return stamps.map((stamp) => ({ ...stamp, marks: stamp.marks.filter((mark) => mark.kind !== "speck") }));
}

/** Each walk drawn as its own pen line, all four at once, with walls and furniture inked as a pen passes them. */
export function composeRoomSheet(sheet: WalkSheet, inkZoom: number) {
  return withoutSpecks(composeInk({ seed: 3, unit: 15, speed: sheet.speed }, (brush) => draftRoom(brush, { sheet, inkZoom })));
}
