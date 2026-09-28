import { Sequence, useCurrentFrame } from "remotion";
import { cue, Soundtrack } from "../../components/Cues";
import { Cutout } from "../../components/Cutout";
import { EndCard } from "../../components/EndCard";
import { Footage } from "../../components/Footage";
import { ScanToPhoto } from "../../components/ScanToPhoto";
import { Grain, Paper, Vignette } from "../../components/Paper";
import { PaperWipe } from "../../components/PaperWipe";
import { progress, sweep } from "../../lib/ease";
import { REEL } from "../../lib/timing";
import { InkFreeze, type Annotation } from "../../components/InkFreeze";
import { BehindType } from "../../components/BehindType";
import { TapeStack as Caption } from "../../components/TapeStack";
import { Punch, Shot } from "../../components/Shot";

const CUTS = { studiers: 75, ceiling: 170, dinner: 240, phone: 335, model: 415, caught: 500, end: 560, length: 632 } as const;
const FREEZE_AT = 26;

const studierNotes: Annotation[] = [
  { kind: "leader", target: [320, 1310], shelf: [560, 760], text: "measuring (again)" },
  { kind: "leader", target: [160, 1790], shelf: [300, 1600], text: "the people he’s interrupting" },
];

const dinnerNotes: Annotation[] = [
  { kind: "leader", target: [236, 936], shelf: [420, 470], text: "still measuring" },
  { kind: "leader", target: [360, 1250], shelf: [560, 1500], text: "dinner" },
];

function Opener() {
  return (
    <Punch push={0.0012}>
      <Footage clip="boris-door" />
      <BehindType lines={["POV:"]} top={690} />
      <Cutout clip="boris-door" frameCount={120} />
      <Caption lines={["your friend won’t stop", "measuring the library"]} at={10} />
    </Punch>
  );
}

function Freeze({ clip, still, notes, seed, caption }: { clip: string; still: string; notes: Annotation[]; seed: number; caption: string[] }) {
  const frame = useCurrentFrame();
  return (
    <>
      {frame < FREEZE_AT ? <Shot clip={clip} startFrom={still.endsWith("90") ? 64 : 49} /> : null}
      <Sequence from={FREEZE_AT} layout="none">
        <InkFreeze still={still} notes={notes} seed={seed} />
      </Sequence>
      <Caption lines={caption} />
    </>
  );
}

function Ceiling() {
  return (
    <Punch push={0.0015}>
      <Footage clip="zihao-ceiling" startFrom={10} />
      <BehindType lines={["the", "ceiling?"]} top={1130} at={6} tone="ink" />
      <Cutout clip="zihao-ceiling" frameCount={135} startFrom={10} />
      <Caption lines={["they did the ceiling too"]} at={20} />
    </Punch>
  );
}

function IntoThePhone() {
  const frame = useCurrentFrame();
  const dive = progress(frame, 50, 30, (t) => t * t * t);
  return (
    <div className="absolute inset-0 overflow-hidden">
      <div className="absolute inset-0" style={{ transform: `scale(${1 + dive * 5})`, transformOrigin: "42% 58%" }}>
        <Shot clip="phone-chair" />
      </div>
      <div className="absolute inset-0 bg-night" style={{ opacity: progress(frame, 72, 8) }} />
      <Caption lines={["“it’s for our startup”"]} at={8} />
    </div>
  );
}

function Model() {
  const frame = useCurrentFrame();
  const measured = 0.55 + 0.45 * progress(frame, 0, 30, sweep);
  return (
    <div className="absolute inset-0 bg-night">
      <ScanToPhoto
        scan="moffett"
        width={REEL.width}
        height={REEL.height}
        measured={measured}
        painted={progress(frame, 30, 26, sweep)}
        camera={{ azimuth: -0.5 + frame * 0.006, elevation: 0.6 + measured * 0.32, distance: 82 - measured * 8 }}
      />
      <Caption lines={["ok the 3D model of", "the library goes hard"]} at={30} />
    </div>
  );
}

function Caught() {
  return (
    <Shot clip="zihao-caught">
      <Caption lines={["follow for more measuring"]} at={8} />
    </Shot>
  );
}

const freezeCues = (at: number) => [cue(at + FREEZE_AT, "shutter", 0.9), cue(at + FREEZE_AT + 2, "scan", 0.7), cue(at + FREEZE_AT, "m-record-stop", 1)];

const cues = [
  cue(0, "hit", 0.6),
  cue(10, "tape", 0.7),
  cue(15, "tape", 0.6),
  ...[CUTS.studiers, CUTS.ceiling, CUTS.dinner, CUTS.phone, CUTS.caught].map((at) => cue(at, "hit", 0.55)),
  cue(CUTS.studiers + 6, "tape", 0.6),
  cue(CUTS.studiers + 11, "tape", 0.5),
  ...freezeCues(CUTS.studiers),
  cue(CUTS.ceiling + 6, "whoosh", 0.5),
  cue(CUTS.ceiling + 20, "tape", 0.6),
  cue(CUTS.ceiling + 22, "m-gasp", 0.6),
  cue(CUTS.dinner + 6, "tape", 0.6),
  ...freezeCues(CUTS.dinner),
  cue(CUTS.phone + 8, "tape", 0.6),
  cue(CUTS.phone + 40, "riser", 0.8),
  cue(CUTS.model, "boom", 0.9),
  cue(CUTS.model + 2, "scan", 0.8),
  cue(CUTS.model + 30, "tape", 0.6),
  cue(CUTS.model + 30, "m-vine-boom", 0.9),
  cue(CUTS.model + 35, "tape", 0.5),
  cue(CUTS.caught + 8, "tape", 0.6),
  cue(CUTS.end, "whoosh-down", 0.6),
  cue(CUTS.end + 10, "hit", 0.5),
  cue(CUTS.end + 24, "pop", 0.5),
];

export const POV_DURATION = CUTS.length;

export function PovReel() {
  return (
    <Paper tone="night">
      <Sequence durationInFrames={CUTS.studiers} layout="none">
        <Opener />
      </Sequence>
      <Sequence from={CUTS.studiers} durationInFrames={CUTS.ceiling - CUTS.studiers} layout="none">
        <Freeze clip="boris-studiers" still="boris-studiers-90" notes={studierNotes} seed={11} caption={["while everyone’s", "trying to study"]} />
      </Sequence>
      <Sequence from={CUTS.ceiling} durationInFrames={CUTS.dinner - CUTS.ceiling} layout="none">
        <Ceiling />
      </Sequence>
      <Sequence from={CUTS.dinner} durationInFrames={CUTS.phone - CUTS.dinner} layout="none">
        <Freeze clip="zihao-glass" still="zihao-glass-75" notes={dinnerNotes} seed={23} caption={["even at dinner"]} />
      </Sequence>
      <Sequence from={CUTS.phone} durationInFrames={CUTS.model - CUTS.phone} layout="none">
        <IntoThePhone />
      </Sequence>
      <Sequence from={CUTS.model} durationInFrames={CUTS.caught - CUTS.model} layout="none">
        <Model />
      </Sequence>
      <Sequence from={CUTS.caught} durationInFrames={CUTS.end - CUTS.caught + 20} layout="none">
        <Caught />
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
