import "./theme.css";
import "./fonts";
import { Composition } from "remotion";
import { FPS, REEL } from "./lib/timing";
import { batchA } from "./reels/batch-a";
import { batchB } from "./reels/batch-b";
import { batchC } from "./reels/batch-c";
import { INCHES_DURATION, InchesReel } from "./reels/inches/InchesReel";
import { ONE_LINE, OneLineReel } from "./reels/oneline/OneLineReel";
import { POV_DURATION, PovReel } from "./reels/pov/PovReel";
import type { ReelSpec } from "./reels/registry";

const firstReels: ReelSpec[] = [
  { id: "Inches", component: InchesReel, durationInFrames: INCHES_DURATION },
  { id: "Pov", component: PovReel, durationInFrames: POV_DURATION },
  { id: "OneLine", component: OneLineReel, durationInFrames: ONE_LINE.length },
];

export function Root() {
  return (
    <>
      {[...firstReels, ...batchA, ...batchB, ...batchC].map((reel) => (
        <Composition key={reel.id} id={reel.id} component={reel.component} durationInFrames={reel.durationInFrames} fps={FPS} width={REEL.width} height={REEL.height} />
      ))}
    </>
  );
}
