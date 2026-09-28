import { useCurrentFrame } from "remotion";
import { drawn, progress } from "../lib/ease";
import { LogoMark } from "./Logo";
import { RevealLines } from "./Type";

/** The sign-off every reel lands on: the mark stretching its dimension lines open, then the name and where to find it. */
export function EndCard({ at = 0 }: { at?: number }) {
  const frame = useCurrentFrame();
  const url = progress(frame, at + 26, 16, drawn);
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-14">
      <LogoMark at={at} size={260} />
      <RevealLines lines={["Standard Physics"]} at={at + 14} className="reel-copy block text-center text-title" />
      <p className="reel-caption -mt-8 text-caption text-ink-muted" style={{ opacity: url, transform: `translateY(${(1 - url) * 20}px)` }}>
        standardphysics.app
      </p>
    </div>
  );
}
