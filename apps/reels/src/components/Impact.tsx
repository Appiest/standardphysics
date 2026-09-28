import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";

const IMPACT_FRAMES = 14;

function wobble(frame: number, salt: number) {
  return Math.sin(frame * 2.7 + salt) * 0.6 + Math.sin(frame * 5.3 + salt * 2.1) * 0.4;
}

function shakeAt(frame: number, hits: readonly number[], strength: number) {
  const hit = hits.filter((at) => frame >= at && frame < at + IMPACT_FRAMES).at(-1);
  if (hit === undefined) return { x: 0, y: 0, scale: 1 };
  const decay = Math.pow(1 - (frame - hit) / IMPACT_FRAMES, 2);
  return { x: wobble(frame, 1.3) * strength * decay, y: wobble(frame, 4.1) * strength * decay, scale: 1 + 0.02 * decay };
}

/** Knocks the whole frame about for a moment after each hit, the way a camera jolts when something heavy lands. */
export function Impact({ hits, strength = 18, children }: { hits: readonly number[]; strength?: number; children: ReactNode }) {
  const frame = useCurrentFrame();
  const { x, y, scale } = shakeAt(frame, hits, strength);
  return (
    <div className="absolute inset-0" style={{ transform: `translate(${x}px, ${y}px) scale(${scale})` }}>
      {children}
    </div>
  );
}
