import { Img, Sequence, staticFile, useCurrentFrame } from "remotion";
import { cue, Soundtrack } from "../../components/Cues";
import { EndCard } from "../../components/EndCard";
import { LidarRoom } from "../../components/LidarRoom";
import { Grain, Paper, Vignette } from "../../components/Paper";
import { PaperWipe } from "../../components/PaperWipe";
import { Shot } from "../../components/Shot";
import { REEL } from "../../lib/timing";
import { ExtraSounds, TapeLines, TapeNote, type ExtraCue } from "./kit";

const CUTS = { splat: 0, floor: 112, ready: 186, white: 256, raw: 356, works: 426, end: 492, length: 556 } as const;
const WHITE_PAGE_AT = 60;

function RawScan() {
  const frame = useCurrentFrame();
  return (
    <div className="absolute inset-0 bg-night">
      <LidarRoom scan="moffett" width={REEL.width} height={REEL.height} reveal={1} cutaway={2.3} radius={40} camera={{ azimuth: 1.2 + frame * 0.008, elevation: 0.45, distance: 40, target: [22, 0.2, -10] }} />
      <TapeLines lines={["5. a raw scan", "before cleanup"]} at={4} />
    </div>
  );
}

function ItWorks() {
  const frame = useCurrentFrame();
  return (
    <Paper>
      <div className="absolute left-1/2 top-[560px] w-[1000px] overflow-hidden rounded-[24px] shadow-[0_30px_90px_rgba(13,13,12,0.35)]" style={{ translate: "-50% 0", transform: `scale(${1 + frame * 0.002})` }}>
        <Img src={staticFile("stills/app-window.png")} className="block w-full" />
      </div>
      <TapeLines lines={["anyway,", "it works now"]} size="hook" at={2} />
      <TapeLines lines={["(mostly)"]} at={22} top={1230} />
    </Paper>
  );
}

const cues = [
  cue(0, "hit", 0.6),
  cue(0, "tape", 0.7),
  cue(44, "tape", 0.7),
  cue(62, "tape", 0.6),
  ...[CUTS.floor, CUTS.ready, CUTS.white, CUTS.raw, CUTS.works].map((at) => cue(at, "hit", 0.55)),
  cue(CUTS.floor + 4, "tape", 0.6),
  cue(CUTS.floor + 22, "tape", 0.7),
  cue(CUTS.floor + 24, "m-buzzer", 0.5),
  cue(CUTS.ready + 4, "tape", 0.6),
  cue(CUTS.ready + 26, "tape", 0.6),
  cue(CUTS.white + 4, "tape", 0.6),
  cue(CUTS.white + WHITE_PAGE_AT + 4, "tape", 0.7),
  cue(CUTS.raw + 4, "tape", 0.6),
  cue(CUTS.works + 2, "tape", 0.7),
  cue(CUTS.works + 22, "tape", 0.5),
  cue(CUTS.works + 24, "m-boing", 0.5),
  cue(76, "m-trombone", 0.6),
  cue(CUTS.ready + 34, "m-clown-horn", 0.55),
  cue(CUTS.white + WHITE_PAGE_AT + 8, "m-vine-boom", 0.9),
  cue(CUTS.end, "whoosh-down", 0.6),
  cue(CUTS.end + 10, "hit", 0.5),
];

const dings: ExtraCue[] = [{ at: CUTS.works + 4, sound: "a-ding", volume: 0.6 }];

export const FLOP_LENGTH = CUTS.length;

export function FlopCore() {
  return (
    <Paper tone="night">
      <Sequence durationInFrames={CUTS.floor} layout="none">
        <Shot clip="a-splat">
          <Sequence durationInFrames={40} layout="none">
            <TapeNote at={-8}>things that went wrong building an app that measures rooms</TapeNote>
          </Sequence>
          <Sequence from={40} layout="none">
            <TapeLines lines={["1. our “photoreal” 3D model", "why does it look haunted"]} at={4} />
          </Sequence>
        </Shot>
      </Sequence>
      <Sequence from={CUTS.floor} durationInFrames={CUTS.ready - CUTS.floor} layout="none">
        <Shot clip="a-floor-nag">
          <TapeLines lines={["2. the app, every few seconds:"]} at={4} />
          <TapeLines lines={["“point the phone at the floor.”"]} at={22} top={900} size="hook" />
        </Shot>
      </Sequence>
      <Sequence from={CUTS.ready} durationInFrames={CUTS.white - CUTS.ready} layout="none">
        <Shot clip="a-ready">
          <TapeLines lines={["3. the app: “Ready.”", "also the app: still uploading"]} at={4} top={1260} />
        </Shot>
      </Sequence>
      <Sequence from={CUTS.white} durationInFrames={CUTS.raw - CUTS.white} layout="none">
        <Shot clip="a-white" startFrom={135}>
          <TapeLines lines={["4. running 1,000 tests"]} at={4} />
          <TapeLines lines={["and then the page went white"]} at={WHITE_PAGE_AT + 4} top={400} />
        </Shot>
      </Sequence>
      <Sequence from={CUTS.raw} durationInFrames={CUTS.works - CUTS.raw} layout="none">
        <RawScan />
      </Sequence>
      <Sequence from={CUTS.works} durationInFrames={CUTS.end - CUTS.works + 20} layout="none">
        <ItWorks />
      </Sequence>
      <Sequence from={CUTS.end} layout="none">
        <PaperWipe>
          <EndCard at={10} />
        </PaperWipe>
      </Sequence>
      <Soundtrack bed="bed-pov" bedVolume={0.35} cues={cues} />
      <ExtraSounds cues={dings} />
      <Vignette strength={0.3} />
      <Grain strength={0.1} />
    </Paper>
  );
}
