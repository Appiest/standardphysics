import type { ReactNode } from "react";
import { Audio, Sequence, staticFile, useCurrentFrame } from "remotion";
import { Footage } from "../../components/Footage";
import { LogoMark } from "../../components/Logo";
import { TapeLabel } from "../../components/Type";
import { drawn, progress } from "../../lib/ease";

export type Beat = { clip: string; from: number; length: number; startFrom?: number; origin?: string };

/** One stock shot: it lands a touch close and settles, then keeps drifting in so the frame never sits still. */
export function Clip({ clip, startFrom = 0, origin = "50% 50%" }: { clip: string; startFrom?: number; origin?: string }) {
  const frame = useCurrentFrame();
  const settle = progress(frame, 0, 12, drawn);
  const scale = 1.08 - 0.08 * settle + frame * 0.0009;
  return (
    <div className="absolute inset-0 overflow-hidden">
      <div className="absolute inset-0" style={{ transform: `scale(${scale})`, transformOrigin: origin }}>
        <Footage clip={`stock-${clip}`} startFrom={startFrom} />
      </div>
    </div>
  );
}

/** Hard cuts between stock shots, each running for its own number of frames. */
export function Montage({ beats }: { beats: readonly Beat[] }) {
  return (
    <>
      {beats.map((beat) => (
        <Sequence key={`${beat.clip}-${beat.from}`} from={beat.from} durationInFrames={beat.length} layout="none">
          <Clip clip={beat.clip} startFrom={beat.startFrom} origin={beat.origin} />
        </Sequence>
      ))}
    </>
  );
}

export type TapeSize = "caption" | "title" | "headline";

const tapeSize: Record<TapeSize, string> = { caption: "text-caption", title: "text-title", headline: "text-headline" };

type TapeLinesProps = { lines: readonly string[]; at: number; exitAt?: number; size?: TapeSize; stagger?: number; className?: string };

/** A caption set as separate strips of masking tape, one per line, so each line lands on its own beat. */
export function TapeLines({ lines, at, exitAt, size = "title", stagger = 4, className }: TapeLinesProps) {
  return (
    <div className={`flex flex-col items-start gap-3 ${className ?? ""}`}>
      {lines.map((line, index) => (
        <TapeLabel
          key={`${line}-${index}`}
          at={at + index * stagger}
          exitAt={exitAt === undefined ? undefined : exitAt + index * 2}
          tilt={index % 2 === 0 ? -1.5 : 1.2}
          className={`reel-copy whitespace-nowrap ${tapeSize[size]}`}
        >
          {line}
        </TapeLabel>
      ))}
    </div>
  );
}

/** A small sign-off: the mark and the name on a strip of tape, so the brand is present without taking over. */
export function SignOff({ at }: { at: number }) {
  const frame = useCurrentFrame();
  const shown = progress(frame, at, 14, drawn);
  return (
    <div className="absolute inset-x-0 flex justify-center" style={{ bottom: 470, opacity: shown, transform: `translateY(${(1 - shown) * 30}px)` }}>
      <div className="flex items-center gap-5 bg-paper-raised/95 px-7 py-4 shadow-[0_10px_30px_rgba(13,13,12,0.3)]">
        <LogoMark at={at} size={72} />
        <span className="reel-copy text-label">Standard Physics</span>
      </div>
    </div>
  );
}

export type FileCue = { at: number; file: string; volume?: number };

/** Sound files outside the shared kit, placed on the frames they belong to. */
export function FileCues({ cues }: { cues: readonly FileCue[] }) {
  return (
    <>
      {cues.map((item, index) => (
        <Sequence key={index} from={item.at} layout="none">
          <Audio src={staticFile(`sound/${item.file}.wav`)} volume={item.volume ?? 1} />
        </Sequence>
      ))}
    </>
  );
}

export function Stage({ children }: { children: ReactNode }) {
  return <div className="absolute inset-x-safe-side top-safe-top">{children}</div>;
}
