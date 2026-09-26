import { Sequence } from "remotion";
import { BehindType } from "../../components/BehindType";
import { cue, Soundtrack } from "../../components/Cues";
import { Cutout } from "../../components/Cutout";
import { EndCard } from "../../components/EndCard";
import { Footage } from "../../components/Footage";
import { InkFreeze, type Annotation } from "../../components/InkFreeze";
import { Grain, Paper, Vignette } from "../../components/Paper";
import { PaperWipe } from "../../components/PaperWipe";
import { Punch, Shot } from "../../components/Shot";
import { SideType, TapeLines } from "./kit";
import { ModelSweep, PhoneDive } from "./scenes";

const CUTS = { wall: 62, ceiling: 146, phone: 212, model: 282, caught: 372, end: 414, length: 478 } as const;
const FREEZE_AT = 22;

const wallNotes: Annotation[] = [
  { kind: "leader", target: [450, 925], shelf: [620, 600], text: "measuring" },
  { kind: "leader", target: [930, 1180], shelf: [640, 1480], text: "not measured yet" },
];

function Opener() {
  return (
    <Punch push={0.0012}>
      <Footage clip="boris-door" />
      <BehindType lines={["POV:"]} top={300} at={-8} />
      <Cutout clip="boris-door" frameCount={120} />
      <TapeLines lines={["your library isn’t", "ADA compliant"]} size="hook" at={-10} top={620} />
    </Punch>
  );
}

function WallFreeze() {
  return (
    <>
      <Shot clip="boris-wall" startFrom={70 - FREEZE_AT} />
      <Sequence from={FREEZE_AT} layout="none">
        <InkFreeze still="boris-wall-70" notes={wallNotes} seed={31} />
      </Sequence>
      <TapeLines lines={["so you measure", "it yourself"]} at={4} />
    </>
  );
}

function Ceiling() {
  return (
    <Punch push={0.0015}>
      <Footage clip="zihao-ceiling" startFrom={10} />
      <SideType lines={["every", "inch"]} top={1080} at={4} />
      <Cutout clip="zihao-ceiling" frameCount={135} startFrom={10} />
      <TapeLines lines={["yes, the ceiling too"]} at={12} />
    </Punch>
  );
}

const cues = [
  cue(0, "hit", 0.7),
  cue(2, "tape", 0.7),
  cue(6, "tape", 0.6),
  ...[CUTS.wall, CUTS.ceiling, CUTS.phone, CUTS.caught].map((at) => cue(at, "hit", 0.55)),
  cue(CUTS.wall + 4, "tape", 0.6),
  cue(CUTS.wall + FREEZE_AT, "shutter", 0.9),
  cue(CUTS.wall + FREEZE_AT + 2, "scan", 0.7),
  cue(CUTS.wall + FREEZE_AT + 12, "scratch", 0.5),
  cue(CUTS.ceiling + 4, "whoosh", 0.5),
  cue(CUTS.ceiling + 12, "tape", 0.6),
  cue(CUTS.phone + 6, "tape", 0.6),
  cue(CUTS.phone + 30, "riser", 0.8),
  cue(CUTS.model, "boom", 0.9),
  cue(CUTS.model + 2, "scan", 0.8),
  cue(CUTS.model + 24, "tape", 0.6),
  cue(CUTS.caught + 6, "tape", 0.6),
  cue(CUTS.end, "whoosh-down", 0.6),
  cue(CUTS.end + 10, "hit", 0.5),
  cue(CUTS.end + 24, "pop", 0.5),
];

export const LIBRARY_POV_LENGTH = CUTS.length;

export function LibraryPov() {
  return (
    <Paper tone="night">
      <Sequence durationInFrames={CUTS.wall} layout="none">
        <Opener />
      </Sequence>
      <Sequence from={CUTS.wall} durationInFrames={CUTS.ceiling - CUTS.wall} layout="none">
        <WallFreeze />
      </Sequence>
      <Sequence from={CUTS.ceiling} durationInFrames={CUTS.phone - CUTS.ceiling} layout="none">
        <Ceiling />
      </Sequence>
      <Sequence from={CUTS.phone} durationInFrames={CUTS.model - CUTS.phone} layout="none">
        <PhoneDive clip="phone-wall" diveAt={CUTS.model - CUTS.phone - 24}>
          <TapeLines lines={["with just a phone"]} at={6} />
        </PhoneDive>
      </Sequence>
      <Sequence from={CUTS.model} durationInFrames={CUTS.caught - CUTS.model} layout="none">
        <ModelSweep>
          <TapeLines lines={["and now the whole", "library is in 3D"]} at={24} />
        </ModelSweep>
      </Sequence>
      <Sequence from={CUTS.caught} durationInFrames={CUTS.end - CUTS.caught + 20} layout="none">
        <Shot clip="zihao-caught">
          <TapeLines lines={["ok it’s kind of fun"]} at={6} />
        </Shot>
      </Sequence>
      <Sequence from={CUTS.end} layout="none">
        <PaperWipe>
          <EndCard at={10} />
        </PaperWipe>
      </Sequence>
      <Soundtrack bed="bed-pov" bedVolume={0.4} cues={cues} />
      <Vignette strength={0.3} />
      <Grain strength={0.1} />
    </Paper>
  );
}
