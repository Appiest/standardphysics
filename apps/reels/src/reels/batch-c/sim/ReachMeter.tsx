import { useCurrentFrame } from "remotion";
import { progress, sweep } from "../../../lib/ease";

const SCALE_CM = 70;
const MODEL_LIMIT_CM = 60;
const REACHED_CM = 59.6;

/** A bar that fills to the reach the simulation measured, with the model's 60 cm limit lit as a line just past it. */
export function ReachMeter({ at, frames }: { at: number; frames: number }) {
  const frame = useCurrentFrame();
  const filled = progress(frame, at, frames, sweep) * REACHED_CM;
  const limitShare = MODEL_LIMIT_CM / SCALE_CM;
  return (
    <div className="absolute inset-x-safe-side top-[1470px]">
      <div className="flex items-baseline justify-between">
        <p className="reel-copy figures text-title text-paper-raised">{filled.toFixed(1)} cm</p>
        <p className="reel-caption figures text-caption text-tape-light">limit {MODEL_LIMIT_CM} cm</p>
      </div>
      <div className="relative mt-4 h-[34px] bg-paper-raised/15">
        <div className="absolute inset-y-0 left-0 bg-paper-raised" style={{ width: `${(filled / SCALE_CM) * 100}%` }} />
        <div className="absolute -inset-y-3 w-[6px] bg-tape-light shadow-[0_0_18px_6px_rgba(246,190,26,0.8)]" style={{ left: `${limitShare * 100}%` }} />
      </div>
    </div>
  );
}

export const REACH = { reachedCm: REACHED_CM, limitCm: MODEL_LIMIT_CM } as const;
