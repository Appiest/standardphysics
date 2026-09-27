import { Sequence } from "remotion";
import { cue, repeatCue, Soundtrack } from "../../components/Cues";
import { EndCard } from "../../components/EndCard";
import { Impact } from "../../components/Impact";
import { Grain, Paper, Vignette } from "../../components/Paper";
import { PaperWipe } from "../../components/PaperWipe";
import { FIELD, FieldScene } from "./FieldScene";
import { MEASURE, MeasureScene } from "./MeasureScene";
import { PHONE, PhoneScene } from "./PhoneScene";
import { PROOF, ProofScene } from "./ProofScene";

const fieldFrom = MEASURE.end - 8;
const phoneFrom = fieldFrom + FIELD.barAt;
const proofFrom = phoneFrom + PHONE.sweepEnd + 6;
const endFrom = proofFrom + PROOF.end;
const END_LENGTH = 84;

export const INCHES_DURATION = endFrom + END_LENGTH;

const cues = [
  cue(0, "whoosh", 0.9),
  ...repeatCue(2, 36, 3, "tick", 0.35),
  cue(MEASURE.flagsAt, "hit", 0.7),
  cue(MEASURE.flagsAt + 6, "tape", 0.6),
  cue(MEASURE.zoomEnd - 42, "riser", 0.8),
  cue(MEASURE.zoomEnd, "m-vine-boom"),
  cue(MEASURE.damagesAt, "whoosh-down", 0.4),
  cue(MEASURE.damagesAt + 34, "hit", 0.9),
  cue(MEASURE.damagesAt + 34, "m-buzzer", 0.55),
  cue(MEASURE.collapseAt, "whoosh-down", 0.6),
  cue(MEASURE.end - 4, "pop", 0.7),
  ...repeatCue(fieldFrom + FIELD.fillStart, fieldFrom + FIELD.fillEnd, 2, "tick", 0.18),
  cue(fieldFrom + FIELD.collapseAt, "whoosh", 0.6),
  cue(phoneFrom - 4, "hit", 0.8),
  cue(phoneFrom, "scan", 0.8),
  cue(phoneFrom + PHONE.firstTape, "tape", 0.7),
  cue(phoneFrom + PHONE.secondTape, "tape", 0.7),
  cue(phoneFrom + PHONE.sweepStart, "scan", 0.9),
  cue(phoneFrom + PHONE.sweepStart + 20, "whoosh", 0.4),
  cue(proofFrom, "whoosh", 0.7),
  cue(proofFrom + 10, "tape", 0.7),
  cue(proofFrom + PROOF.findingAt, "hit", 0.8),
  cue(endFrom, "whoosh-down", 0.6),
  cue(endFrom + 10, "hit", 0.5),
  cue(endFrom + 24, "pop", 0.5),
];

export function InchesReel() {
  return (
    <Paper>
      <Impact hits={[MEASURE.flagsAt, MEASURE.zoomEnd, MEASURE.damagesAt + 34, phoneFrom, proofFrom + 58]}>
      <Sequence durationInFrames={MEASURE.end} layout="none">
        <MeasureScene />
      </Sequence>
      <Sequence from={fieldFrom} durationInFrames={FIELD.end} layout="none">
        <FieldScene />
      </Sequence>
      <Sequence from={phoneFrom} durationInFrames={endFrom - phoneFrom + 20} layout="none">
        <PhoneScene />
      </Sequence>
      <Sequence from={proofFrom} durationInFrames={PROOF.end + 20} layout="none">
        <ProofScene />
      </Sequence>
      <Sequence from={endFrom} layout="none">
        <PaperWipe>
          <EndCard at={10} />
        </PaperWipe>
      </Sequence>
      </Impact>
      <Soundtrack bed="bed-inches" cues={cues} />
      <Vignette strength={0.28} />
      <Grain strength={0.12} />
    </Paper>
  );
}
