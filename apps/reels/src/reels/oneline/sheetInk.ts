import { boundsOf } from "../../../../web/src/lib/schematic-brush/geometry";
import type { SchematicBrush } from "../../../../web/src/lib/schematic-brush/brush";
import { composeInk, type Point } from "../../lib/ink";
import { composeRoomSheet, objectCorners } from "./roomInk";
import type { WalkSheet } from "./walkSheet";

export const INK_ZOOM = 1.5;
const NODE_UNIT = 20;
/** Only one walk carries the brush's schematic nodes. Riding all four buries the plan; one keeps the drafting character. */
const NODE_WALK = 2;

/** Schematic nodes along one walk, avoiding the furniture, timed to follow that walk's pen. */
function draftNodes(brush: SchematicBrush, sheet: WalkSheet) {
  const drafting = { sheet, inkZoom: INK_ZOOM };
  sheet.plan.objects.forEach((object) => brush.stampWith((d) => d.occupancy.claim(boundsOf(objectCorners(drafting, object)))));
  sheet.pens[NODE_WALK].forEach((stroke) => {
    const route = stroke.points.map(([x, y]): Point => [x / INK_ZOOM, y / INK_ZOOM]);
    brush.draftRoute(route, stroke.durationMs, stroke.startMs);
  });
}

/** The room as the pens draw it, plus the brush's nodes riding along the walks. */
export function composeSheet(sheet: WalkSheet) {
  const nodes = composeInk({ seed: 11, unit: NODE_UNIT, speed: 1.3 }, (brush) => draftNodes(brush, sheet));
  return [...composeRoomSheet(sheet, INK_ZOOM), ...nodes].sort((a, b) => a.startsAt - b.startsAt);
}
