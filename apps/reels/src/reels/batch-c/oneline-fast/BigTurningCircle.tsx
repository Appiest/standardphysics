import type { Point } from "../../../lib/ink";
import { cumulativeLengths, onSheet, pointAtShare, type SheetFrame } from "../../../lib/plan";

/** ADA 304.3.1: a wheelchair needs a 60 inch circle to turn around in. */
const TURNING_DIAMETER_METRES = 1.524;

type Props = { route: Point[]; frame: SheetFrame; share: number; size: number; opacity: number };

function trailUpTo(route: Point[], share: number) {
  const lengths = cumulativeLengths(route);
  const reached = share * lengths[lengths.length - 1];
  return [...route.filter((_, index) => lengths[index] <= reached), pointAtShare(route, share)];
}

/** The 60 inch turning circle rolling along the walked route, trailing tape-yellow light, labelled at phone size. */
export function BigTurningCircle({ route, frame, share, size, opacity }: Props) {
  const [cx, cy] = onSheet(frame, pointAtShare(route, share));
  const radius = (TURNING_DIAMETER_METRES / 2) * frame.scale;
  const trail = trailUpTo(route, share).map((point) => onSheet(frame, point).join(",")).join(" ");
  return (
    <svg width={size} height={size} className="absolute inset-0 overflow-visible" style={{ opacity, transform: "translateZ(2px)" }}>
      <defs>
        <filter id="fast-tape-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="9" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      <polyline points={trail} fill="none" stroke="#f6be1a" strokeWidth={12} strokeLinecap="round" strokeLinejoin="round" opacity={0.85} filter="url(#fast-tape-glow)" />
      <circle cx={cx} cy={cy} r={radius} fill="rgba(246,190,26,0.24)" stroke="#f6be1a" strokeWidth={7} filter="url(#fast-tape-glow)" />
      <circle cx={cx} cy={cy} r={radius} fill="none" stroke="#fff4c7" strokeWidth={2.5} />
    </svg>
  );
}
