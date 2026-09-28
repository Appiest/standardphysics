import { useMemo } from "react";
import { Img, staticFile, useCurrentFrame } from "remotion";
import { leaderNoteAt, dimensionLeft } from "../../../web/src/lib/schematic-brush/annotations";
import type { DraftRecorder } from "../../../web/src/lib/schematic-brush/recorder";
import { InkLayer } from "./InkLayer";
import { Paper } from "./Paper";
import { ScanBar } from "./ScanBar";
import { composeInk, type Point } from "../lib/ink";
import { progress, sweep } from "../lib/ease";
import { REEL, toMs } from "../lib/timing";

export type Annotation =
  | { kind: "leader"; target: Point; shelf: Point; text: string }
  | { kind: "height"; top: Point; bottom: Point; lineX: number; text: string };

const SWEEP_FRAMES = 12;
const INK_ZOOM = 2.6;

function atInkScale(note: Annotation): Annotation {
  const half = ([x, y]: Point): Point => [x / INK_ZOOM, y / INK_ZOOM];
  if (note.kind === "leader") return { ...note, target: half(note.target), shelf: half(note.shelf) };
  return { ...note, top: half(note.top), bottom: half(note.bottom), lineX: note.lineX / INK_ZOOM };
}

function annotate(d: DraftRecorder, note: Annotation) {
  if (note.kind === "leader") leaderNoteAt(d, note.target, note.shelf, note.text);
  else dimensionLeft(d, note.top, note.bottom, note.lineX, note.text);
}

/** A held frame that the scan bar redraws as a pen drawing on paper, then marks up like a site survey. */
export function InkFreeze({ still, notes, seed }: { still: string; notes: Annotation[]; seed: number }) {
  const frame = useCurrentFrame();
  const stamps = useMemo(
    () => composeInk({ seed, unit: 22, speed: 1.4 }, (brush) => notes.forEach((note, index) => brush.stampWith((d) => annotate(d, atInkScale(note)), index * 380))),
    [notes, seed],
  );
  const flash = 1 - progress(frame, 0, 6);
  const edge = progress(frame, 2, SWEEP_FRAMES, sweep);
  const inkStart = 2 + SWEEP_FRAMES;
  return (
    <div className="absolute inset-0">
      <Img src={staticFile(`stills/${still}-photo.jpg`)} className="absolute inset-0 size-full" />
      <div className="absolute inset-0" style={{ clipPath: `inset(0 0 ${(1 - edge) * 100}% 0)` }}>
        <Paper>
          <Img src={staticFile(`stills/${still}-ink.png`)} className="absolute inset-0 size-full opacity-55" />
        </Paper>
      </div>
      <InkLayer stamps={stamps} timeMs={toMs(Math.max(0, frame - inkStart))} width={REEL.width} height={REEL.height} zoom={INK_ZOOM} className="absolute inset-0" />
      {edge > 0 && edge < 1 && <ScanBar at={edge} trail={0.14} />}
      <div className="absolute inset-0 bg-paper-raised" style={{ opacity: flash }} />
    </div>
  );
}
