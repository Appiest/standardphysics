import { Sequence, useCurrentFrame } from "remotion";
import { facts } from "../../../../web/src/lib/facts";
import { cue, repeatCue, Soundtrack } from "../../components/Cues";
import { Grain, Paper, Vignette } from "../../components/Paper";
import { Shot } from "../../components/Shot";
import { TapeLabel } from "../../components/Type";
import { progress } from "../../lib/ease";
import { LawsuitField } from "../inches/LawsuitField";
import { MeasureScene } from "../inches/MeasureScene";
import { ExtraSounds, Slam } from "./kit";
import { ModelSweep } from "./scenes";

const BEATS = { therapy: 34, ruler: 92, model: 147, dots: 205, length: 279 } as const;
const RULER_FROM = 95;

function NeedTherapy() {
  return (
    <Paper>
      <div className="absolute inset-x-safe-side top-[640px]">
        <Slam at={-4} className="reel-copy text-shout">
          I need
          <br />
          therapy.
        </Slam>
      </div>
    </Paper>
  );
}

function Sunflower() {
  const frame = useCurrentFrame();
  const fill = progress(frame, 0, 60, (t) => t * t * (3 - 2 * t));
  return (
    <Paper>
      <LawsuitField count={facts.adaLawsuitsFiled2025.value} state={{ shown: 1 + fill * (facts.adaLawsuitsFiled2025.value - 1), rotation: frame * 0.012, collapse: 0, opacity: 1 }} />
    </Paper>
  );
}

function TheTherapy() {
  return (
    <div className="absolute inset-x-safe-side top-safe-top">
      <TapeLabel at={-2} className="reel-copy text-title">
        The therapy:
      </TapeLabel>
    </div>
  );
}

const cues = [
  cue(0, "hit", 0.5),
  cue(BEATS.therapy, "tape", 0.8),
  cue(BEATS.therapy, "hit", 0.5),
  cue(BEATS.therapy + 4, "scratch", 0.4),
  ...repeatCue(BEATS.ruler, BEATS.ruler + 36, 3, "tick", 0.3),
  cue(BEATS.ruler + 38, "boom", 0.6),
  cue(BEATS.model, "scan", 0.8),
  cue(BEATS.model + 20, "scan", 0.5),
  ...repeatCue(BEATS.dots, BEATS.dots + 60, 3, "pop", 0.18),
];

export const THERAPY_LENGTH = BEATS.length;

export function TherapyMeme() {
  return (
    <Paper>
      <Sequence durationInFrames={BEATS.therapy} layout="none">
        <NeedTherapy />
      </Sequence>
      <Sequence from={BEATS.therapy} durationInFrames={BEATS.ruler - BEATS.therapy} layout="none">
        <Shot clip="boris-wall" />
      </Sequence>
      <Sequence from={BEATS.ruler} durationInFrames={BEATS.model - BEATS.ruler} layout="none">
        <Sequence from={-RULER_FROM} layout="none">
          <Paper>
            <MeasureScene />
          </Paper>
        </Sequence>
      </Sequence>
      <Sequence from={BEATS.model} durationInFrames={BEATS.dots - BEATS.model} layout="none">
        <ModelSweep startReveal={0.35} spin={0.006} paintAt={20} />
      </Sequence>
      <Sequence from={BEATS.dots} layout="none">
        <Sunflower />
      </Sequence>
      <Sequence from={BEATS.therapy} layout="none">
        <TheTherapy />
      </Sequence>
      <Soundtrack bed="bed-oneline" bedVolume={0.5} cues={cues} loops />
      <ExtraSounds cues={[{ at: BEATS.therapy, sound: "a-ding", volume: 0.5 }]} />
      <Vignette strength={0.25} />
      <Grain strength={0.1} />
    </Paper>
  );
}
