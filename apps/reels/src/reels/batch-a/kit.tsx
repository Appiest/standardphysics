import type { ReactNode } from "react";
import { Audio, Sequence, staticFile, useCurrentFrame } from "remotion";
import { TapeLabel } from "../../components/Type";
import { drawn, progress } from "../../lib/ease";

export type ExtraSound = "a-womp" | "a-ding" | "a-sparkle" | "a-clack";
export type ExtraCue = { at: number; sound: ExtraSound; volume?: number };

/** Batch A's own one-shots, played alongside the shared Soundtrack. */
export function ExtraSounds({ cues }: { cues: readonly ExtraCue[] }) {
  return (
    <>
      {cues.map((item, index) => (
        <Sequence key={index} from={item.at} layout="none">
          <Audio src={staticFile(`sound/${item.sound}.wav`)} volume={item.volume ?? 1} />
        </Sequence>
      ))}
    </>
  );
}

type TapeLinesProps = { lines: readonly string[]; at?: number; exitAt?: number; size?: "hook" | "caption"; top?: number };

const tapeSize = { hook: "reel-copy text-title", caption: "reel-caption text-caption" } as const;

/** Masking-tape captions stacked down from the top of the frame; the hook size is for the line that has to land in the first second. */
export function TapeLines({ lines, at = 0, exitAt, size = "caption", top }: TapeLinesProps) {
  return (
    <div className="absolute inset-x-safe-side flex flex-col items-start gap-4" style={{ top: top ?? 250 }}>
      {lines.map((line, index) => (
        <TapeLabel key={line} at={at + index * 4} exitAt={exitAt === undefined ? undefined : exitAt + index * 2} tilt={index % 2 === 0 ? -2 : 1.5} className={tapeSize[size]}>
          {line}
        </TapeLabel>
      ))}
    </div>
  );
}

/** Big type that lands on a beat: it arrives slightly large and snaps down, which reads as a hit rather than a fade. */
export function Slam({ children, at, className, style }: { children: ReactNode; at: number; className?: string; style?: React.CSSProperties }) {
  const frame = useCurrentFrame();
  const landed = progress(frame, at, 8, drawn);
  if (frame < at) return null;
  return (
    <div className={className} style={{ ...style, opacity: Math.min(1, landed * 3), transform: `scale(${1.35 - 0.35 * landed})` }}>
      {children}
    </div>
  );
}

/** Typed text for the "AI will replace you" style openers: two characters a frame, with the caret still blinking after. */
export function Typed({ text, at, className }: { text: string; at: number; className?: string }) {
  const frame = useCurrentFrame();
  const shown = Math.max(0, Math.min(text.length, (frame - at) * 2));
  const caretOn = shown < text.length || Math.floor(frame / 8) % 2 === 0;
  return (
    <p className={className}>
      {text.slice(0, shown)}
      <span className="inline-block w-[0.08em] bg-ink align-baseline" style={{ height: "0.8em", opacity: caretOn ? 1 : 0 }} />
    </p>
  );
}

export const clackCues = (at: number, characters: number): ExtraCue[] =>
  Array.from({ length: Math.ceil(characters / 2 / 2) }, (_, index) => ({ at: at + index * 2, sound: "a-clack", volume: 0.5 }));

/** Ink type set down the left edge beside someone rather than behind them, for when a person fills the middle of the frame. */
export function SideType({ lines, top, at = 0 }: { lines: readonly string[]; top: number; at?: number }) {
  const frame = useCurrentFrame();
  return (
    <div className="absolute left-safe-side" style={{ top }}>
      {lines.map((line, index) => {
        const rise = progress(frame, at + index * 4, 16, drawn);
        return (
          <div key={line} className="overflow-hidden">
            <div className="reel-copy text-shout text-ink" style={{ transform: `translateY(${(1 - rise) * 105}%)`, textShadow: "0 0 40px rgba(243,243,241,0.6)" }}>
              {line}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** One torn strip holding a whole sentence that wraps inside it, for hooks too long to split into tidy one-line tapes. */
export function TapeNote({ children, at = 0, top = 250 }: { children: ReactNode; at?: number; top?: number }) {
  return (
    <div className="absolute inset-x-safe-side" style={{ top }}>
      <TapeLabel at={at} className="reel-copy text-title" style={{ textWrap: "balance" }}>
        {children}
      </TapeLabel>
    </div>
  );
}
