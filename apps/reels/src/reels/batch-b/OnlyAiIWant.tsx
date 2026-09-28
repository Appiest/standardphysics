import { useCurrentFrame } from "remotion";
import { facts } from "../../../../web/src/lib/facts";
import { cue, Soundtrack } from "../../components/Cues";
import { Grain, Paper, Vignette } from "../../components/Paper";
import { drawn, progress, snap } from "../../lib/ease";
import { Montage, SignOff, Stage, TapeLines, type Beat } from "./kit";

const T = { paperwork: 64, pages: 98, makers: 150, agree: 318, end: 420 } as const;

const makers = ["cake", "florist", "latte", "tailor", "dough", "potter", "florist-hand", "pastry"];
const MAKER_BEAT = 21;

const beats: Beat[] = [
  { clip: "b-pottery", from: 0, length: T.paperwork },
  { clip: "b-paperwork", from: T.paperwork, length: T.makers - T.paperwork },
  ...makers.map((clip, index) => ({ clip: `b-${clip}`, from: T.makers + index * MAKER_BEAT, length: MAKER_BEAT, startFrom: 20 })),
  { clip: "b-latte", from: T.agree, length: T.end - T.agree, startFrom: 150 },
];

function PageStack() {
  const frame = useCurrentFrame();
  const land = progress(frame, T.pages, 12, snap);
  const leave = progress(frame, T.makers - 8, 10, drawn);
  const pages = Math.round(progress(frame, T.pages, 22, drawn) * facts.adaStandardsPages.value);
  if (frame < T.pages || leave >= 1) return null;
  return (
    <div
      className="absolute inset-x-safe-side"
      style={{ top: 860, opacity: 1 - leave, transform: `translateY(${(1 - land) * 500 + leave * 300}px) rotate(${(1 - land) * 8 - 2}deg)` }}
    >
      {[18, 12, 6].map((offset) => (
        <div key={offset} className="absolute inset-0 bg-paper-sunken shadow-[0_8px_24px_rgba(13,13,12,0.25)]" style={{ transform: `translate(${offset}px, ${offset}px)` }} />
      ))}
      <div className="relative bg-paper-raised px-12 py-10 shadow-[0_20px_60px_rgba(13,13,12,0.35)]">
        <p className="reel-copy figures text-poster">{pages}</p>
        <p className="reel-caption text-caption">pages of accessibility rules</p>
        <p className="mt-4 font-body text-fineprint text-ink-muted">{facts.adaStandardsPages.source}</p>
      </div>
    </div>
  );
}

const cues = [
  cue(2, "tape", 0.7),
  cue(6, "tape", 0.6),
  cue(T.paperwork, "whoosh", 0.4),
  cue(T.paperwork + 4, "tape", 0.7),
  cue(T.paperwork + 8, "tape", 0.6),
  cue(T.pages, "hit", 0.7),
  ...makers.map((_, index) => cue(T.makers + index * MAKER_BEAT, "tick", 0.5)),
  cue(T.makers + 8, "tape", 0.6),
  cue(T.makers + 12, "tape", 0.6),
  cue(T.makers + 16, "tape", 0.6),
  cue(T.agree + 4, "hit", 0.6),
  cue(T.agree + 30, "pop", 0.5),
];

export const ONLY_AI_DURATION = T.end;

export function OnlyAiIWant() {
  return (
    <Paper tone="night">
      <Montage beats={beats} />
      <Stage>
        <TapeLines lines={["I don’t want AI", "making the art."]} at={2} exitAt={T.paperwork - 6} />
      </Stage>
      <Stage>
        <TapeLines lines={["I want it doing", "the paperwork."]} at={T.paperwork + 4} exitAt={T.makers - 6} />
      </Stage>
      <PageStack />
      <Stage>
        <TapeLines lines={["so the people who", "make things can", "keep making them."]} at={T.makers + 8} exitAt={T.agree - 6} size="title" />
      </Stage>
      <Stage>
        <TapeLines lines={["like if", "you agree"]} at={T.agree + 4} size="headline" />
      </Stage>
      <SignOff at={T.agree + 30} />
      <Soundtrack bed="b-warm" bedVolume={0.45} cues={cues} />
      <Vignette strength={0.3} />
      <Grain strength={0.08} />
    </Paper>
  );
}
