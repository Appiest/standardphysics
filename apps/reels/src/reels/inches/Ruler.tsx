import { REEL } from "../../lib/timing";

export type RulerView = {
  /** The inch mark that sits in the middle of the frame. */
  center: number;
  pixelsPerInch: number;
};

export const RULER = { top: 1010, height: 360 } as const;

type Tick = { inch: number; length: number; weight: number };

function sixteenthLength(step: number) {
  if (step % 16 === 0) return 150;
  if (step % 8 === 0) return 104;
  if (step % 4 === 0) return 76;
  if (step % 2 === 0) return 54;
  return 36;
}

function visibleRange({ center, pixelsPerInch }: RulerView) {
  const halfSpan = REEL.width / 2 / pixelsPerInch;
  return [center - halfSpan - 0.1, center + halfSpan + 0.1] as const;
}

function ticksEvery(view: RulerView, divisions: number, length: (step: number) => number): Tick[] {
  const [from, to] = visibleRange(view);
  const first = Math.ceil(from * divisions);
  const last = Math.floor(to * divisions);
  if (last - first > 1600) return [];
  return Array.from({ length: last - first + 1 }, (_, index) => {
    const step = first + index;
    return { inch: step / divisions, length: length(step), weight: step % divisions === 0 ? 5 : 2.4 };
  });
}

function tenthLength(step: number) {
  if (step % 10 === 0) return 150;
  if (step % 5 === 0) return 90;
  return 56;
}

export function xOfInch(view: RulerView, inch: number) {
  return REEL.width / 2 + (inch - view.center) * view.pixelsPerInch;
}

function RulerFace({ view }: { view: RulerView }) {
  const top = RULER.top;
  const bottom = RULER.top + RULER.height;
  const fractions = ticksEvery(view, 16, sixteenthLength);
  const tenths = ticksEvery(view, 10, tenthLength);
  const wholeInches = fractions.filter((tick) => Number.isInteger(tick.inch));
  return (
    <g>
      <rect x={-40} y={top} width={REEL.width + 80} height={RULER.height} className="fill-paper-raised" />
      <line x1={-40} x2={REEL.width + 40} y1={top} y2={top} className="stroke-ink" strokeWidth={6} />
      <line x1={-40} x2={REEL.width + 40} y1={bottom} y2={bottom} className="stroke-ink" strokeWidth={6} />
      {fractions.map((tick) => (
        <line key={`f${tick.inch}`} x1={xOfInch(view, tick.inch)} x2={xOfInch(view, tick.inch)} y1={top} y2={top + tick.length} className="stroke-ink" strokeWidth={tick.weight} />
      ))}
      {tenths.map((tick) => (
        <line key={`t${tick.inch}`} x1={xOfInch(view, tick.inch)} x2={xOfInch(view, tick.inch)} y1={bottom} y2={bottom - tick.length * 0.7} className="stroke-ink-muted" strokeWidth={tick.weight * 0.8} />
      ))}
      {wholeInches.map((tick) => (
        <text key={`n${tick.inch}`} x={xOfInch(view, tick.inch) + 14} y={top + 250} className="reel-copy fill-ink" fontSize={96}>
          {tick.inch}
        </text>
      ))}
    </g>
  );
}

/** A drafting scale: sixteenths along the top edge, tenths along the bottom. Fast pans leave ghosted copies behind as motion blur. */
export function Ruler({ view, velocity }: { view: RulerView; velocity: number }) {
  const ghosts = Math.abs(velocity) > 8 ? [0.2, 0.4, 0.6, 0.8] : [];
  return (
    <svg width={REEL.width} height={REEL.height} className="absolute inset-0 overflow-visible">
      {ghosts.map((share) => (
        <g key={share} opacity={0.22 * (1 - share)} transform={`translate(${velocity * share}, 0)`}>
          <RulerFace view={view} />
        </g>
      ))}
      <RulerFace view={view} />
    </svg>
  );
}
