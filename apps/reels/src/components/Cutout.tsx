import { Img, staticFile, useCurrentFrame } from "remotion";

/** The person from a b-roll clip, cut out of their background frame by frame, so type can sit behind them. */
export function Cutout({ clip, frameCount, startFrom = 0 }: { clip: string; frameCount: number; startFrom?: number }) {
  const frame = Math.min(frameCount, useCurrentFrame() + startFrom + 1);
  return <Img src={staticFile(`cutouts/${clip}/f_${String(frame).padStart(4, "0")}.webp`)} className="absolute inset-0 size-full" />;
}
