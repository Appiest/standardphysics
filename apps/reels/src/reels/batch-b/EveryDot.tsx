import { useCurrentFrame } from "remotion";
import { facts } from "../../../../web/src/lib/facts";
import { cue, repeatCue, Soundtrack } from "../../components/Cues";
import { FinePrint } from "../../components/FinePrint";
import { GlowDot } from "../../components/Glow";
import { Grain, Paper, Vignette } from "../../components/Paper";
import { RevealLines } from "../../components/Type";
import { drawn, progress } from "../../lib/ease";
import { LawsuitField, lawsuitFieldCenter } from "../inches/LawsuitField";
import { SignOff, TapeLines } from "./kit";

const lawsuits = facts.adaLawsuitsFiled2025.value;
const counted = new Intl.NumberFormat("en-US");
const T = { fillStart: 4, fillEnd: 96, count: 104, fix: 192, like: 262, end: 336 } as const;
const FIELD = { scale: 0.78, drop: 170 } as const;

function shownAt(frame: number) {
  const fill = progress(frame, T.fillStart, T.fillEnd - T.fillStart, (t) => t * t * (3 - 2 * t));
  return 1 + fill * (lawsuits - 1);
}

function Field() {
  const frame = useCurrentFrame();
  const rotation = frame * 0.005 + progress(frame, 0, T.fillEnd, drawn) * 1.4;
  return (
    <div className="absolute inset-0" style={{ transform: `translateY(${FIELD.drop}px) scale(${FIELD.scale})`, transformOrigin: `50% ${lawsuitFieldCenter.y}px` }}>
      <LawsuitField count={lawsuits} state={{ shown: shownAt(frame), rotation, collapse: 0, opacity: 1 }} />
      <GlowDot x={lawsuitFieldCenter.x} y={lawsuitFieldCenter.y} size={40} />
    </div>
  );
}

function Count() {
  const frame = useCurrentFrame();
  const shown = progress(frame, T.count, 14, drawn);
  const gone = progress(frame, T.fix - 8, 10, drawn);
  return (
    <div className="absolute inset-x-safe-side top-safe-top" style={{ opacity: shown * (1 - gone), transform: `translateY(${(1 - shown) * 40 - gone * 40}px)` }}>
      <p className="reel-copy figures text-poster">{counted.format(Math.round(shownAt(frame)))}</p>
      <p className="reel-caption mt-6 text-title">lawsuits in 2025</p>
      <FinePrint at={T.count + 10} className="mt-4">
        {facts.adaLawsuitsFiled2025.source}
      </FinePrint>
    </div>
  );
}

const cues = [
  cue(0, "pop", 0.6),
  ...repeatCue(T.fillStart, T.fillEnd, 2, "tick", 0.2),
  cue(T.count, "hit", 0.7),
  cue(T.fix, "whoosh", 0.4),
  cue(T.like, "tape", 0.7),
  cue(T.like + 30, "pop", 0.5),
];

export const EVERY_DOT_DURATION = T.end;

export function EveryDot() {
  return (
    <Paper>
      <Field />
      <div className="absolute inset-x-safe-side top-safe-top">
        <RevealLines lines={["Every dot is a", "lawsuit filed over", "accessibility", "last year."]} at={0} exitAt={T.count - 12} stagger={3} className="reel-copy block text-title" />
      </div>
      <Count />
      <div className="absolute inset-x-safe-side top-safe-top">
        <RevealLines lines={["Fixing it should be", "easier than", "getting sued."]} at={T.fix} stagger={3} className="reel-copy block text-title" />
        <div className="mt-8">
          <TapeLines lines={["like if you agree"]} at={T.like} />
        </div>
      </div>
      <SignOff at={T.like + 30} />
      <Soundtrack bed="bed-inches" bedVolume={0.4} cues={cues} />
      <Vignette strength={0.22} />
      <Grain strength={0.1} />
    </Paper>
  );
}
