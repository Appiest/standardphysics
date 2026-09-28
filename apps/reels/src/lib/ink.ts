import { SchematicBrush } from "../../../web/src/lib/schematic-brush/brush";
import type { Stamp } from "../../../web/src/lib/schematic-brush/marks";
import { withSeed } from "./seeded";

export type { Stamp };
export type { Point, Box } from "../../../web/src/lib/schematic-brush/geometry";

/** Glyph widths only steer where labels may go, so an estimate keeps every render tab laying the sheet out identically. */
const estimateTextWidth = (text: string, size: number) => text.length * size * 0.56;

type InkOptions = { seed: number; unit: number; speed?: number };

export function composeInk({ seed, unit, speed = 1 }: InkOptions, draft: (brush: SchematicBrush) => void): Stamp[] {
  return withSeed(seed, () => {
    const brush = new SchematicBrush({ unit, speed, measureText: estimateTextWidth, now: () => 0 });
    draft(brush);
    return brush.takeAll().sort((a, b) => a.startsAt - b.startsAt);
  });
}

export function inkDuration(stamps: readonly Stamp[]) {
  return stamps.reduce((latest, stamp) => Math.max(latest, stamp.endsAt), 0);
}
