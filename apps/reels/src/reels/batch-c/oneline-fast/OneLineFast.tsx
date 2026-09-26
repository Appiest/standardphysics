import { useMemo } from "react";
import { useCurrentFrame } from "remotion";
import { cue, repeatCue, Soundtrack } from "../../../components/Cues";
import { GlowDot } from "../../../components/Glow";
import { InkLayer } from "../../../components/InkLayer";
import { Grain, Paper, Vignette } from "../../../components/Paper";
import { RevealLines } from "../../../components/Type";
import { drawn, mix, progress, sweep } from "../../../lib/ease";
import { inkDuration } from "../../../lib/ink";
import { centroid, onSheet } from "../../../lib/plan";
import { useFloorPlan, type FloorPlan } from "../../../lib/scan";
import { FPS, REEL, toMs } from "../../../lib/timing";
import { PaperModel } from "../../oneline/PaperModel";
import { TitleBlock } from "../../oneline/TitleBlock";
import { buildWalkSheet, penAt, type WalkSheet } from "../../oneline/walkSheet";
import { MOFFITT_WALK_MINUTES } from "../../oneline/walks";
import { BigTurningCircle } from "./BigTurningCircle";
import { composeFastSheet, FAST_INK_ZOOM } from "./fastSheetInk";

/** The stage is a window onto the sheet; pushing into an aisle overflows it, so it clips. */
const STAGE = { top: 440, width: REEL.width, height: 880 } as const;
const SCAN = "moffett";

/** Twelve seconds, so the loop lands on the fast bed's own loop point. */
export const ONE_LINE_FAST = {
  walkFrames: 78,
  tiltStart: 84,
  tiltFrames: 22,
  riseStart: 92,
  checkStart: 114,
  zoomFrames: 24,
  checkFrames: 100,
  flattenStart: 250,
  rewindStart: 280,
  rewindFrames: 56,
  length: 360,
} as const;

const T = ONE_LINE_FAST;
const WALK_MS = (T.walkFrames / FPS) * 1000;
const AISLE_ZOOM = 3.2;

type Sheet = WalkSheet & { stamps: ReturnType<typeof composeFastSheet>; inkEnd: number };

function useSheet(plan: FloorPlan | null): Sheet | null {
  return useMemo(() => {
    if (!plan) return null;
    const walkSheet = buildWalkSheet(plan, { x: 30, y: 20, width: STAGE.width - 60, height: STAGE.height - 40 }, WALK_MS, FAST_INK_ZOOM);
    const stamps = composeFastSheet(walkSheet);
    return { ...walkSheet, stamps, inkEnd: inkDuration(stamps) };
  }, [plan]);
}

const cues = [
  cue(0, "scratch", 0.7),
  cue(T.tiltStart, "whoosh", 0.6),
  ...repeatCue(T.riseStart, T.riseStart + 20, 2, "pop", 0.25),
  cue(T.checkStart, "whoosh", 0.45),
  cue(T.checkStart + T.zoomFrames, "scan", 0.7),
  cue(T.checkStart + 70, "scan", 0.5),
  cue(T.flattenStart - 22, "whoosh-down", 0.45),
  cue(T.rewindStart, "whoosh", 0.5),
  cue(T.rewindStart + 4, "scratch", 0.4),
];

function inkTime(frame: number, inkEnd: number) {
  const forward = Math.min(inkEnd, toMs(frame));
  return forward * (1 - progress(frame, T.rewindStart, T.rewindFrames, sweep));
}

/** 0 on the flat plan, 1 fully pushed into the aisle. */
function aisleZoom(frame: number) {
  return progress(frame, T.checkStart, T.zoomFrames, sweep) * (1 - progress(frame, T.flattenStart - 24, 22, sweep));
}

function sheetTransform(frame: number, aisleOffset: readonly [number, number]) {
  const tilt = progress(frame, T.tiltStart, T.tiltFrames, sweep) * (1 - progress(frame, T.flattenStart, 22, sweep));
  const zoom = aisleZoom(frame);
  const scale = mix(1, AISLE_ZOOM, zoom) * (1 - tilt * 0.1);
  return `rotateX(${tilt * 54}deg) rotateZ(${tilt * -32}deg) scale(${scale}) translate(${-aisleOffset[0] * zoom}px, ${-aisleOffset[1] * zoom}px)`;
}

function riseOf(frame: number, count: number) {
  const falling = progress(frame, T.flattenStart - 6, 18, sweep);
  return (index: number) => progress(frame, T.riseStart + (index / count) * 18, 12, drawn) * (1 - falling);
}

function Captions() {
  const frame = useCurrentFrame();
  const returning = frame >= T.rewindStart;
  return (
    <div className="absolute inset-x-safe-side top-safe-top">
      {returning ? (
        <RevealLines lines={["Walk a library", "once."]} at={T.rewindStart + 30} className="reel-copy block text-headline" />
      ) : (
        <RevealLines lines={["Walk a library", "once."]} at={-40} exitAt={T.tiltStart - 6} className="reel-copy block text-headline" />
      )}
      <div className="absolute inset-x-0 top-0">
        <RevealLines lines={["It stands up every", "table and chair."]} at={T.tiltStart + 2} exitAt={T.checkStart - 8} className="reel-copy block text-title" />
      </div>
      <div className="absolute inset-x-0 top-0">
        <RevealLines lines={["Then it checks where", "a wheelchair can turn."]} at={T.checkStart + 12} exitAt={T.flattenStart - 6} className="reel-copy block text-title" />
      </div>
    </div>
  );
}

function Pens({ sheet, frame }: { sheet: Sheet; frame: number }) {
  const shown = frame < T.walkFrames + 4 || frame > T.rewindStart;
  if (!shown) return null;
  const timeMs = inkTime(frame, sheet.inkEnd);
  return sheet.pens.map((strokes, index) => {
    const at = penAt(strokes, timeMs);
    return at ? <GlowDot key={index} x={at[0]} y={at[1]} size={24} /> : null;
  });
}

function SheetStage({ sheet }: { sheet: Sheet }) {
  const frame = useCurrentFrame();
  const aisleCenter = onSheet(sheet.frame, centroid(sheet.aisle));
  const aisleOffset = [aisleCenter[0] - STAGE.width / 2, aisleCenter[1] - STAGE.height / 2] as const;
  const checking = progress(frame, T.checkStart + T.zoomFrames - 6, T.checkFrames, (t) => t * t * (3 - 2 * t));
  const circleShown = progress(frame, T.checkStart + 10, 8) * (1 - progress(frame, T.flattenStart - 26, 10));
  return (
    <div className="absolute overflow-hidden" style={{ left: 0, top: STAGE.top, width: STAGE.width, height: STAGE.height, perspective: 2400 }}>
      <div className="absolute inset-0" style={{ transformStyle: "preserve-3d", transform: sheetTransform(frame, aisleOffset) }}>
        <InkLayer stamps={sheet.stamps} timeMs={inkTime(frame, sheet.inkEnd)} width={STAGE.width} height={STAGE.height} zoom={FAST_INK_ZOOM} className="absolute inset-0" />
        <PaperModel objects={sheet.plan.objects} frame={sheet.frame} riseOf={riseOf(frame, sheet.plan.objects.length)} />
        {circleShown > 0 && <BigTurningCircle route={sheet.aisle} frame={sheet.frame} share={checking} size={STAGE.width} opacity={circleShown} />}
        <Pens sheet={sheet} frame={frame} />
      </div>
    </div>
  );
}

/** The one-line reel on Moffitt: four walks ink the library in two and a half seconds, it stands up, an aisle gets checked, and it rewinds into a loop. */
export function OneLineFast() {
  const plan = useFloorPlan(SCAN);
  const sheet = useSheet(plan);
  return (
    <Paper>
      {sheet && <SheetStage sheet={sheet} />}
      <Captions />
      {sheet && (
        <TitleBlock
          facts={[
            { label: "Walks", value: String(sheet.pens.length) },
            { label: "Walking", value: `${MOFFITT_WALK_MINUTES} min` },
            { label: "Path", value: `${sheet.metres} m` },
            { label: "Objects", value: String(sheet.plan.objects.length) },
          ]}
        />
      )}
      <Soundtrack bed="c-bed-fastline" bedVolume={0.5} cues={cues} loops />
      <Vignette strength={0.22} />
      <Grain strength={0.1} />
    </Paper>
  );
}
