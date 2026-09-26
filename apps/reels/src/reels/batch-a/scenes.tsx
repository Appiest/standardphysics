import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { ScanToPhoto } from "../../components/ScanToPhoto";
import { Shot } from "../../components/Shot";
import { progress, sweep } from "../../lib/ease";
import { REEL } from "../../lib/timing";

/** Phone-in-hand footage that pushes into the screen and goes dark, ready to cut to the model. */
export function PhoneDive({ clip, diveAt, children }: { clip: string; diveAt: number; children?: ReactNode }) {
  const frame = useCurrentFrame();
  const dive = progress(frame, diveAt, 24, (t) => t * t * t);
  return (
    <div className="absolute inset-0 overflow-hidden">
      <div className="absolute inset-0" style={{ transform: `scale(${1 + dive * 5})`, transformOrigin: "42% 55%" }}>
        <Shot clip={clip} />
      </div>
      <div className="absolute inset-0 bg-night" style={{ opacity: progress(frame, diveAt + 18, 6) }} />
      {children}
    </div>
  );
}

type ModelSweepProps = { children?: ReactNode; spin?: number; startReveal?: number; paintAt?: number };

/** The Moffitt floor on black: the LiDAR measures it in as clay, then the scan bar comes back and paints it with the walk's photos. */
export function ModelSweep({ children, spin = 0.004, startReveal = 0.5, paintAt = 30 }: ModelSweepProps) {
  const frame = useCurrentFrame();
  const measured = startReveal + (1 - startReveal) * progress(frame, 0, 26, sweep);
  const painted = progress(frame, paintAt, 26, sweep);
  return (
    <div className="absolute inset-0 bg-night">
      <ScanToPhoto
        scan="moffett"
        width={REEL.width}
        height={REEL.height}
        measured={measured}
        painted={painted}
        camera={{ azimuth: 0.4 + frame * spin, elevation: 0.72 + measured * 0.2, distance: 86 - measured * 8 }}
      />
      {children}
    </div>
  );
}
