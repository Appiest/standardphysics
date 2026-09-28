import { interpolate, useCurrentFrame } from "remotion";
import { facts } from "../../../../web/src/lib/facts";
import { FinePrint } from "../../components/FinePrint";
import { GlowDot } from "../../components/Glow";
import { RevealLines } from "../../components/Type";
import { between, drawn, progress, slam, snap, sweep } from "../../lib/ease";
import { REEL } from "../../lib/timing";
import { Ruler, RULER, xOfInch, type RulerView } from "./Ruler";

const MEASURED = 31.8;
const REQUIRED = 32;
const GAP_CENTER = (MEASURED + REQUIRED) / 2;
const RULER_MIDDLE = RULER.top + RULER.height / 2;

export const MEASURE = { whipEnd: 40, flagsAt: 44, secondLine: 50, zoomStart: 88, zoomEnd: 130, damagesAt: 150, collapseAt: 222, end: 238 } as const;

function rulerView(frame: number): RulerView {
  const whip = progress(frame, 0, MEASURE.whipEnd, drawn);
  const zoom = progress(frame, MEASURE.zoomStart, MEASURE.zoomEnd - MEASURE.zoomStart, sweep);
  const creep = progress(frame, MEASURE.whipEnd, MEASURE.zoomStart - MEASURE.whipEnd, (t) => t);
  const center = interpolate(whip, [0, 1], [6, GAP_CENTER - 0.25]) + creep * 0.08 + zoom * 0.17;
  return { center, pixelsPerInch: (320 + creep * 90) * Math.pow(4600 / 410, zoom) };
}

function rulerVelocity(frame: number) {
  const now = rulerView(frame);
  const before = rulerView(Math.max(0, frame - 1));
  return (now.center - before.center) * now.pixelsPerInch;
}

function MarkerFlag({ view, inch, label, tone, at }: { view: RulerView; inch: number; label: string; tone: "fail" | "ink"; at: number }) {
  const frame = useCurrentFrame();
  const drop = progress(frame, at, 10, snap);
  const x = xOfInch(view, inch);
  const color = tone === "fail" ? "bg-fail" : "bg-ink";
  const above = tone === "fail";
  const flagTop = above ? RULER.top - 150 : RULER.top + RULER.height + 30;
  return (
    <div className="absolute" style={{ left: x, top: 0, opacity: Math.min(1, drop * 2) }}>
      <div className={`absolute w-[6px] -translate-x-1/2 ${color}`} style={{ top: RULER.top - 60, height: RULER.height + 120, transform: `scaleY(${drop})` }} />
      <div
        className={`reel-copy figures absolute whitespace-nowrap px-5 py-2 text-label text-paper-raised ${color}`}
        style={{ top: flagTop, left: above ? -16 : 16, transform: `translateY(${(1 - drop) * (above ? -40 : 40)}px)`, translate: above ? "-100% 0" : "0 0" }}
      >
        {label}
      </div>
    </div>
  );
}

function GapLight({ view, frame }: { view: RulerView; frame: number }) {
  const left = xOfInch(view, MEASURED);
  const right = xOfInch(view, REQUIRED);
  const lit = progress(frame, MEASURE.flagsAt + 8, 20);
  return (
    <div
      aria-hidden
      className="absolute mix-blend-multiply"
      style={{
        left,
        width: right - left,
        top: RULER.top,
        height: RULER.height,
        opacity: lit,
        background: "linear-gradient(to bottom, rgba(246,190,26,0.55), rgba(246,190,26,0.85) 50%, rgba(246,190,26,0.55))",
        boxShadow: "0 0 80px 30px rgba(246,190,26,0.45)",
      }}
    />
  );
}

function GapLabel({ frame }: { frame: number }) {
  const shown = progress(frame, MEASURE.zoomEnd - 8, 10, slam);
  const leaving = progress(frame, MEASURE.damagesAt + 6, 10, sweep);
  return (
    <div
      className="reel-copy figures absolute inset-x-0 text-center text-poster text-ink"
      style={{ top: RULER_MIDDLE - 130, opacity: shown * (1 - leaving), transform: `scale(${(2 - shown) * (1 - leaving * 0.4)})` }}
    >
      0.2 in
    </div>
  );
}

function Headlines({ frame }: { frame: number }) {
  return (
    <div className="absolute inset-x-safe-side top-safe-top">
      <RevealLines lines={["Doorways need", "32 inches."]} at={8} exitAt={MEASURE.zoomStart - 6} className="reel-copy block text-headline" />
      <div className="mt-8" style={{ opacity: frame < MEASURE.secondLine ? 0 : 1 }}>
        <RevealLines lines={["We scanned one", "at 31.8."]} at={MEASURE.secondLine} exitAt={MEASURE.zoomStart - 4} className="reel-copy block text-headline text-ink-muted" />
      </div>
    </div>
  );
}

function Damages({ frame }: { frame: number }) {
  if (frame < MEASURE.damagesAt) return null;
  const collapse = progress(frame, MEASURE.collapseAt, MEASURE.end - MEASURE.collapseAt, sweep);
  const amount = progress(frame, MEASURE.damagesAt + 22, 12, slam);
  return (
    <div className="absolute inset-0" style={{ opacity: Math.max(0, 1 - collapse * 2.2), transform: `scale(${1 - collapse * 0.96})`, transformOrigin: `50% ${RULER_MIDDLE}px` }}>
      <div className="absolute inset-x-safe-side top-safe-top">
        <RevealLines lines={["In California, a door", "like that can cost a", "business at least"]} at={MEASURE.damagesAt} className="reel-caption block text-title" />
      </div>
      <div className="reel-copy figures absolute inset-x-0 text-center text-amount" style={{ top: RULER_MIDDLE - 120, transform: `scale(${1.6 - amount * 0.6})`, opacity: amount }}>
        $4,000
      </div>
      <FinePrint at={MEASURE.damagesAt + 40} className="absolute inset-x-safe-side bottom-safe-bottom">
        {facts.californiaMinimumDamages.source}, minimum damages per violation
      </FinePrint>
    </div>
  );
}

export function MeasureScene() {
  const frame = useCurrentFrame();
  const view = rulerView(frame);
  const rulerGone = between(frame, [MEASURE.damagesAt, MEASURE.damagesAt + 16], [1, 0]);
  const dot = progress(frame, MEASURE.collapseAt + 4, 12, snap);
  return (
    <div className="absolute inset-0">
      <div className="absolute inset-0" style={{ opacity: rulerGone }}>
        <Ruler view={view} velocity={rulerVelocity(frame)} />
        <GapLight view={view} frame={frame} />
        <MarkerFlag view={view} inch={MEASURED} label="31.8 in measured" tone="fail" at={MEASURE.flagsAt} />
        <MarkerFlag view={view} inch={REQUIRED} label="32 in needed" tone="ink" at={MEASURE.flagsAt + 6} />
      </div>
      <Headlines frame={frame} />
      <GapLabel frame={frame} />
      <Damages frame={frame} />
      {dot > 0 && <GlowDot x={REEL.width / 2} y={RULER_MIDDLE} size={34 * dot} />}
    </div>
  );
}

export const measureDotY = RULER_MIDDLE;
