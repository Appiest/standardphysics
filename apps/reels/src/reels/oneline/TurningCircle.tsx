import { cumulativeLengths, onSheet, pointAtShare, type SheetFrame } from "../../lib/plan";
import type { Point } from "../../lib/ink";

/** ADA 304.3.1: a wheelchair needs a 60 inch circle to turn around in. */
const TURNING_DIAMETER_METRES = 1.524;

type TurningCircleProps = { route: Point[]; frame: SheetFrame; share: number; size: number; opacity: number };

function trailUpTo(route: Point[], share: number) {
  const lengths = cumulativeLengths(route);
  const reached = share * lengths[lengths.length - 1];
  return [...route.filter((_, index) => lengths[index] <= reached), pointAtShare(route, share)];
}

export function TurningCircle({ route, frame, share, size, opacity }: TurningCircleProps) {
  const [cx, cy] = onSheet(frame, pointAtShare(route, share));
  const radius = (TURNING_DIAMETER_METRES / 2) * frame.scale;
  const trail = trailUpTo(route, share).map((point) => onSheet(frame, point).join(",")).join(" ");
  return (
    <svg width={size} height={size} className="absolute inset-0 overflow-visible" style={{ opacity, transform: "translateZ(2px)" }}>
      <defs>
        <filter id="tape-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="9" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      <polyline points={trail} fill="none" stroke="#f6be1a" strokeWidth={10} strokeLinecap="round" strokeLinejoin="round" opacity={0.85} filter="url(#tape-glow)" />
      <circle cx={cx} cy={cy} r={radius} fill="rgba(246,190,26,0.22)" stroke="#f6be1a" strokeWidth={6} filter="url(#tape-glow)" />
      <circle cx={cx} cy={cy} r={radius} fill="none" stroke="#fff4c7" strokeWidth={2} />
      <text x={cx + radius + 14} y={cy + 10} className="reel-caption fill-ink" fontSize={30}>
        60″ turning space
      </text>
    </svg>
  );
}
