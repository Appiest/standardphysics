import type { ReactNode } from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";

type Tone = "paper" | "night";

const surface: Record<Tone, string> = { paper: "bg-paper", night: "bg-night" };

function Fibers({ id }: { id: string }) {
  return (
    <svg className="absolute inset-0 size-full mix-blend-multiply" aria-hidden>
      <filter id={`${id}-mottle`}>
        <feTurbulence type="fractalNoise" baseFrequency="0.0035" numOctaves={3} seed={9} />
        <feColorMatrix values="0 0 0 0 0.42  0 0 0 0 0.4  0 0 0 0 0.36  0 0 0 0.16 0" />
      </filter>
      <filter id={`${id}-tooth`}>
        <feTurbulence type="fractalNoise" baseFrequency="0.55" numOctaves={2} seed={4} />
        <feColorMatrix values="0 0 0 0 0.3  0 0 0 0 0.3  0 0 0 0 0.28  0 0 0 0.2 0" />
      </filter>
      <rect width="100%" height="100%" filter={`url(#${id}-mottle)`} />
      <rect width="100%" height="100%" filter={`url(#${id}-tooth)`} />
    </svg>
  );
}

export function Grain({ strength = 0.1 }: { strength?: number }) {
  const frame = useCurrentFrame();
  const id = `grain-${frame % 6}`;
  return (
    <svg className="pointer-events-none absolute inset-0 size-full mix-blend-overlay" style={{ opacity: strength }} aria-hidden>
      <filter id={id}>
        <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves={2} seed={frame % 6} stitchTiles="stitch" />
        <feColorMatrix type="saturate" values="0" />
      </filter>
      <rect width="100%" height="100%" filter={`url(#${id})`} />
    </svg>
  );
}

export function Vignette({ strength = 0.35 }: { strength?: number }) {
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0"
      style={{ background: `radial-gradient(120% 90% at 50% 45%, transparent 55%, rgba(13,13,12,${strength}) 100%)` }}
    />
  );
}

export function Paper({ children, tone = "paper" }: { children?: ReactNode; tone?: Tone }) {
  return (
    <AbsoluteFill className={surface[tone]}>
      {tone === "paper" && <Fibers id="paper-fibers" />}
      {children}
    </AbsoluteFill>
  );
}
