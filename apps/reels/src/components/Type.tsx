import type { CSSProperties, ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { drawn, exit, progress, snap } from "../lib/ease";

type RevealLinesProps = {
  lines: readonly string[];
  at: number;
  exitAt?: number;
  stagger?: number;
  className?: string;
};

function lineOffset(frame: number, at: number, exitAt: number | undefined) {
  const entered = progress(frame, at, 18, drawn);
  const left = exitAt === undefined ? 0 : progress(frame, exitAt, 10, exit);
  return (1 - entered) * 112 - left * 112;
}

export function RevealLines({ lines, at, exitAt, stagger = 3, className }: RevealLinesProps) {
  const frame = useCurrentFrame();
  return (
    <span className={className}>
      {lines.map((line, index) => (
        <span key={line} className="-mb-[0.1em] block overflow-hidden pb-[0.1em]">
          <span className="block whitespace-nowrap" style={{ transform: `translateY(${lineOffset(frame, at + index * stagger, exitAt === undefined ? undefined : exitAt + index * 2)}%)` }}>
            {line}
          </span>
        </span>
      ))}
    </span>
  );
}

const tornEdge = "polygon(1.5% 0, 98% 4%, 100% 22%, 98.6% 41%, 100% 63%, 98.4% 82%, 99.6% 100%, 2% 96%, 0 78%, 1.2% 55%, 0 34%, 1.4% 15%)";

type TapeLabelProps = {
  children: ReactNode;
  at: number;
  exitAt?: number;
  tilt?: number;
  className?: string;
  style?: CSSProperties;
};

/** A strip of masking tape slapped onto the frame: it lands a little large and settles flat. */
export function TapeLabel({ children, at, exitAt, tilt = -2, className, style }: TapeLabelProps) {
  const frame = useCurrentFrame();
  const landed = progress(frame, at, 7, snap);
  const peeled = exitAt === undefined ? 0 : progress(frame, exitAt, 8, exit);
  const scale = 1.25 - 0.25 * landed;
  const opacity = Math.min(landed * 3, 1) * (1 - peeled);
  return (
    <div
      className={`relative inline-block bg-paper-raised/95 px-[0.55em] py-[0.28em] text-ink shadow-[0_10px_30px_rgba(13,13,12,0.28)] ${className ?? ""}`}
      style={{ ...style, clipPath: tornEdge, opacity, transform: `rotate(${tilt + peeled * 6}deg) scale(${scale}) translateY(${-peeled * 40}px)` }}
    >
      {children}
    </div>
  );
}
