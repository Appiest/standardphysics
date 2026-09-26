import { useMemo } from "react";
import { useCurrentFrame } from "remotion";
import { cue, repeatCue, Soundtrack } from "../../components/Cues";
import { GlowDot } from "../../components/Glow";
import { InkLayer } from "../../components/InkLayer";
import { Grain, Paper, Vignette } from "../../components/Paper";
import { ScanBar } from "../../components/ScanBar";
import { TexturedRoom } from "../../components/TexturedRoom";
import { RevealLines } from "../../components/Type";
import { drawn, mix, progress, sweep } from "../../lib/ease";
import { inkDuration } from "../../lib/ink";
import { centroid, onSheet } from "../../lib/plan";
import { useFloorPlan, type FloorPlan } from "../../lib/scan";
import { FPS, REEL, toMs } from "../../lib/timing";
import { PaperModel } from "./PaperModel";
import { composeSheet, INK_ZOOM } from "./sheetInk";
import { TitleBlock } from "./TitleBlock";
import { TurningCircle } from "./TurningCircle";
import { buildWalkSheet, penAt, type WalkSheet } from "./walkSheet";
import { MOFFITT_WALK_MINUTES } from "./walks";

/** The stage is a window onto the sheet; pushing into an aisle overflows it, so it clips. */
const STAGE = { top: 440, width: REEL.width, height: 880 } as const;
const SCAN = "moffett";
/** Photos the phone took across the four walks, which the app paints back onto the model. */
const PHOTOS_TAKEN = 3524;
const AISLE_ZOOM = 3.2;

export const ONE_LINE = {
  walkFrames: 120,
  tiltStart: 128,
  tiltFrames: 22,
  riseStart: 136,
  checkStart: 162,
  zoomFrames: 22,
  checkFrames: 80,
  unzoomStart: 262,
  paintStart: 288,
  paintFrames: 30,
  unpaintStart: 384,
  unpaintFrames: 26,
  rewindStart: 416,
  rewindFrames: 52,
  length: 480,
} as const;

const T = ONE_LINE;
const WALK_MS = (T.walkFrames / FPS) * 1000;

type Sheet = WalkSheet & { stamps: ReturnType<typeof composeSheet>; inkEnd: number };

function useSheet(plan: FloorPlan | null): Sheet | null {
  return useMemo(() => {
    if (!plan) return null;
    const walkSheet = buildWalkSheet(plan, { x: 30, y: 20, width: STAGE.width - 60, height: STAGE.height - 40 }, WALK_MS, INK_ZOOM);
    const stamps = composeSheet(walkSheet);
    return { ...walkSheet, stamps, inkEnd: inkDuration(stamps) };
  }, [plan]);
}

const cues = [
  cue(0, "scratch", 0.6),
  cue(90, "scratch", 0.45),
  cue(T.tiltStart, "whoosh", 0.6),
  ...repeatCue(T.riseStart, T.riseStart + 22, 2, "pop", 0.22),
  cue(T.checkStart, "whoosh", 0.45),
  cue(T.checkStart + T.zoomFrames, "scan", 0.7),
  cue(T.unzoomStart, "whoosh-down", 0.4),
  cue(T.paintStart, "scan", 0.9),
  cue(T.paintStart + T.paintFrames, "hit", 0.5),
  cue(T.unpaintStart, "scan", 0.6),
  cue(T.rewindStart, "whoosh", 0.5),
  cue(T.rewindStart + 4, "scratch", 0.35),
];

function inkTime(frame: number, inkEnd: number) {
  const forward = Math.min(inkEnd, toMs(frame));
  return forward * (1 - progress(frame, T.rewindStart, T.rewindFrames, sweep));
}

/** How much of the stage the photo-textured model covers, swept down by the scan bar and lifted back up. */
function painted(frame: number) {
  return progress(frame, T.paintStart, T.paintFrames, sweep) * (1 - progress(frame, T.unpaintStart, T.unpaintFrames, sweep));
}

function sheetTransform(frame: number, aisleOffset: readonly [number, number]) {
  const tilt = progress(frame, T.tiltStart, T.tiltFrames, sweep) * (1 - progress(frame, T.unpaintStart - 20, 18, sweep));
  const zoom = progress(frame, T.checkStart, T.zoomFrames, sweep) * (1 - progress(frame, T.unzoomStart, 22, sweep));
  const scale = mix(1, AISLE_ZOOM, zoom) * (1 - tilt * 0.1);
  return `rotateX(${tilt * 54}deg) rotateZ(${tilt * -32}deg) scale(${scale}) translate(${-aisleOffset[0] * zoom}px, ${-aisleOffset[1] * zoom}px)`;
}

function riseOf(frame: number, count: number) {
  const falling = progress(frame, T.unpaintStart - 26, 18, sweep);
  return (index: number) => progress(frame, T.riseStart + (index / count) * 22, 14, drawn) * (1 - falling);
}

function Captions() {
  const frame = useCurrentFrame();
  const returning = frame >= T.rewindStart;
  return (
    <div className="absolute inset-x-safe-side top-safe-top">
      {returning ? (
        <RevealLines lines={["Walk a library", "once."]} at={T.rewindStart + 26} className="reel-copy block text-headline" />
      ) : (
        <RevealLines lines={["Walk a library", "once."]} at={-40} exitAt={T.tiltStart - 6} className="reel-copy block text-headline" />
      )}
      <div className="absolute inset-x-0 top-0">
        <RevealLines lines={["It stands up every", "table and chair."]} at={T.tiltStart + 2} exitAt={T.checkStart - 8} className="reel-copy block text-title" />
      </div>
      <div className="absolute inset-x-0 top-0">
        <RevealLines lines={["Then it checks where", "a wheelchair can turn."]} at={T.checkStart + 10} exitAt={T.unzoomStart + 6} className="reel-copy block text-title" />
      </div>
      <div className="absolute inset-x-0 top-0">
        <RevealLines lines={["And paints on the", `${PHOTOS_TAKEN.toLocaleString("en-US")} photos it took.`]} at={T.paintStart + 12} exitAt={T.unpaintStart - 4} className="reel-copy block text-title" />
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

function PaperSheet({ sheet }: { sheet: Sheet }) {
  const frame = useCurrentFrame();
  const aisleCenter = onSheet(sheet.frame, centroid(sheet.aisle));
  const aisleOffset = [aisleCenter[0] - STAGE.width / 2, aisleCenter[1] - STAGE.height / 2] as const;
  const checking = progress(frame, T.checkStart + T.zoomFrames - 6, T.checkFrames, (t) => t * t * (3 - 2 * t));
  const circleShown = progress(frame, T.checkStart + 10, 8) * (1 - progress(frame, T.unzoomStart - 4, 10));
  return (
    <div className="absolute inset-0" style={{ transformStyle: "preserve-3d", transform: sheetTransform(frame, aisleOffset) }}>
      <InkLayer stamps={sheet.stamps} timeMs={inkTime(frame, sheet.inkEnd)} width={STAGE.width} height={STAGE.height} zoom={INK_ZOOM} className="absolute inset-0" />
      <PaperModel objects={sheet.plan.objects} frame={sheet.frame} riseOf={riseOf(frame, sheet.plan.objects.length)} />
      {circleShown > 0 && <TurningCircle route={sheet.aisle} frame={sheet.frame} share={checking} size={STAGE.width} opacity={circleShown} />}
      <Pens sheet={sheet} frame={frame} />
    </div>
  );
}

/** The photo-textured Moffitt model, seen from above and turned to line up with the squared plan. */
function PhotoPass({ sheet }: { sheet: Sheet }) {
  const frame = useCurrentFrame();
  const cover = painted(frame);
  if (cover <= 0) return null;
  const camera = { azimuth: -sheet.angle + 0.35 + (frame - T.paintStart) * 0.0025, elevation: 0.95, distance: 96 };
  return (
    <>
      <div className="absolute inset-0 bg-night" style={{ clipPath: `inset(0 0 ${(1 - cover) * 100}% 0)` }}>
        <TexturedRoom scan={SCAN} width={STAGE.width} height={STAGE.height} camera={camera} cutaway={2.3} />
      </div>
      {cover < 1 && <ScanBar at={cover} trail={0.1} />}
    </>
  );
}

function Stage({ sheet }: { sheet: Sheet }) {
  return (
    <div className="absolute overflow-hidden" style={{ left: 0, top: STAGE.top, width: STAGE.width, height: STAGE.height, perspective: 2400 }}>
      <PaperSheet sheet={sheet} />
      <PhotoPass sheet={sheet} />
    </div>
  );
}

/** The one-line reel on Moffitt: four walks ink the library, it stands up, an aisle gets checked, the photos go on, and it rewinds into a loop. */
export function OneLineReel() {
  const plan = useFloorPlan(SCAN);
  const sheet = useSheet(plan);
  return (
    <Paper>
      {sheet && <Stage sheet={sheet} />}
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
      <Soundtrack bed="bed-oneline" bedVolume={0.5} cues={cues} loops />
      <Vignette strength={0.22} />
      <Grain strength={0.1} />
    </Paper>
  );
}
