import type { CSSProperties } from "react";
import { OffthreadVideo, staticFile } from "remotion";

type FootageProps = { clip: string; style?: CSSProperties; className?: string; startFrom?: number; playbackRate?: number };

export function Footage({ clip, style, className, startFrom = 0, playbackRate = 1 }: FootageProps) {
  return (
    <OffthreadVideo
      src={staticFile(`broll/${clip}.mp4`)}
      muted
      startFrom={startFrom}
      playbackRate={playbackRate}
      className={`absolute inset-0 size-full object-cover ${className ?? ""}`}
      style={style}
    />
  );
}
