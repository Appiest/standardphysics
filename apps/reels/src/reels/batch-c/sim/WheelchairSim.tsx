import { Img, Sequence, staticFile, useCurrentFrame } from "remotion";
import { cue, Soundtrack } from "../../../components/Cues";
import { EndCard } from "../../../components/EndCard";
import { FinePrint } from "../../../components/FinePrint";
import { Footage } from "../../../components/Footage";
import { Impact } from "../../../components/Impact";
import { Grain, Paper, Vignette } from "../../../components/Paper";
import { PaperWipe } from "../../../components/PaperWipe";
import { RevealLines } from "../../../components/Type";
import { drawn, progress, slam } from "../../../lib/ease";
import { OneShots, shot } from "../Sounds";
import { REACH, ReachMeter } from "./ReachMeter";

const BEATS = { reach: 108, reachFrames: 87, spare: 195, verdict: 262, scale: 336, end: 416, length: 486 } as const;
const spareCm = (REACH.limitCm - REACH.reachedCm).toFixed(1);

export const WHEELCHAIR_SIM_DURATION = BEATS.length;

const cues = [
  cue(0, "hit", 0.6),
  cue(BEATS.reach, "whoosh", 0.5),
  cue(BEATS.reach + 10, "riser", 0.9),
  cue(BEATS.spare, "boom", 0.9),
  cue(BEATS.verdict, "tape", 0.6),
  cue(BEATS.scale, "whoosh", 0.4),
  cue(BEATS.end, "whoosh-down", 0.6),
  cue(BEATS.end + 10, "hit", 0.5),
];

const shots = [shot(BEATS.spare + 4, "c-chime", 0.5)];

function Panel({ children }: { children: React.ReactNode }) {
  const frame = useCurrentFrame();
  const push = 1 + frame * 0.0006;
  return (
    <div className="absolute inset-x-0 top-[590px] h-[850px] overflow-hidden">
      <div className="absolute inset-0" style={{ transform: `scale(${push})` }}>
        {children}
      </div>
    </div>
  );
}

function Headline({ lines, at = 0, exitAt }: { lines: string[]; at?: number; exitAt?: number }) {
  return (
    <div className="absolute inset-x-safe-side top-safe-top text-paper-raised">
      <RevealLines lines={lines} at={at} exitAt={exitAt} stagger={2} className="reel-copy block text-title" />
    </div>
  );
}

function Spare() {
  const frame = useCurrentFrame();
  const hit = progress(frame, 0, 8, slam);
  return (
    <div className="absolute inset-x-0 top-[760px] text-center" style={{ transform: `scale(${1.6 - 0.6 * hit})`, opacity: hit }}>
      <p className="reel-copy figures text-poster text-tape-light tape-glow">{spareCm} cm</p>
      <p className="reel-copy text-title text-paper-raised">to spare</p>
    </div>
  );
}

function Verdict() {
  const frame = useCurrentFrame();
  const shown = progress(frame, 12, 14, drawn);
  return (
    <>
      <Headline lines={["The sim still", "flagged it."]} />
      <div className="absolute inset-x-safe-side top-[1470px]" style={{ opacity: shown, transform: `translateY(${(1 - shown) * 30}px)` }}>
        <p className="reel-caption text-title text-tape-light">“Near reach limit;</p>
        <p className="reel-caption text-title text-tape-light">needs measurement.”</p>
      </div>
    </>
  );
}

function Scale() {
  return (
    <div className="absolute inset-0 bg-night">
      <Headline lines={["This was one of", "2,000,000 scenarios", "in one scanned room."]} />
      <FinePrint at={20} className="absolute inset-x-safe-side top-[900px] text-paper-raised/70">
        From our simulation replay: an actual LiDAR scan with hypothetical props, and illustrative hand motion. Props and body profiles are simulation assumptions. This does not establish physical task ability or ADA compliance.
      </FinePrint>
    </div>
  );
}

/** A suspense cut of our wheelchair simulation: approach, a slowed reach for a medicine bottle, and the 0.4 cm it clears the model's limit by. */
export function WheelchairSim() {
  return (
    <Paper tone="night">
      <Impact hits={[BEATS.spare]} strength={16}>
        <Sequence durationInFrames={BEATS.reach} layout="none">
          <Panel>
            <Footage clip="c-sim-follow-approach" />
          </Panel>
          <Headline lines={["We gave a virtual", "wheelchair user", "one job: grab the", "medicine bottle."]} exitAt={BEATS.reach - 8} />
        </Sequence>
        <Sequence from={BEATS.reach} durationInFrames={BEATS.spare - BEATS.reach} layout="none">
          <Panel>
            <Footage clip="c-sim-pov-reach" playbackRate={0.5} />
          </Panel>
          <Headline lines={["Its reach limit", "is 60 cm."]} at={2} />
          <ReachMeter at={4} frames={BEATS.reachFrames - 8} />
        </Sequence>
        <Sequence from={BEATS.spare} durationInFrames={BEATS.verdict - BEATS.spare} layout="none">
          <Panel>
            <Img src={staticFile("stills/c-sim-pov-last.png")} className="absolute inset-0 size-full object-cover opacity-45" />
          </Panel>
          <Spare />
        </Sequence>
        <Sequence from={BEATS.verdict} durationInFrames={BEATS.scale - BEATS.verdict} layout="none">
          <Panel>
            <Img src={staticFile("stills/c-sim-follow-last.png")} className="absolute inset-0 size-full object-cover" />
          </Panel>
          <Verdict />
        </Sequence>
        <Sequence from={BEATS.scale} durationInFrames={BEATS.end - BEATS.scale + 20} layout="none">
          <Scale />
        </Sequence>
        <Sequence from={BEATS.end} layout="none">
          <PaperWipe>
            <EndCard at={10} />
          </PaperWipe>
        </Sequence>
      </Impact>
      <Soundtrack bed="c-bed-suspense" bedVolume={0.5} cues={cues} />
      <OneShots shots={shots} />
      <Vignette strength={0.35} />
      <Grain strength={0.08} />
    </Paper>
  );
}
