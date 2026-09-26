import { Sequence, useCurrentFrame } from "remotion";
import { cue, Soundtrack } from "../../components/Cues";
import { GlowDot } from "../../components/Glow";
import { Impact } from "../../components/Impact";
import { TexturedRoom } from "../../components/TexturedRoom";
import { Grain, Paper, Vignette } from "../../components/Paper";
import { Shot } from "../../components/Shot";
import { progress } from "../../lib/ease";
import { REEL } from "../../lib/timing";
import { ExtraSounds, Slam, TapeLines } from "./kit";

/** Beats follow the trend audio: the name, the "woahhh", then "I just got her two weeks ago" (ours: last weekend). */
const BEATS = { reveal: 40, name: 44, woah: 76, got: 104, proud: 196, length: 238 } as const;

const SPARKLES = [
  { x: 150, y: 520, size: 26, phase: 0 },
  { x: 930, y: 610, size: 34, phase: 1.3 },
  { x: 820, y: 1380, size: 22, phase: 2.1 },
  { x: 210, y: 1250, size: 30, phase: 3.4 },
  { x: 560, y: 430, size: 18, phase: 4.2 },
  { x: 990, y: 1080, size: 20, phase: 5.0 },
] as const;

function Sparkles() {
  const frame = useCurrentFrame();
  return (
    <>
      {SPARKLES.map((sparkle) => {
        const twinkle = 0.5 + 0.5 * Math.sin(frame * 0.35 + sparkle.phase);
        return <GlowDot key={sparkle.phase} x={sparkle.x} y={sparkle.y} size={sparkle.size * (0.4 + twinkle)} opacity={0.35 + 0.65 * twinkle} />;
      })}
    </>
  );
}

/** The name shouts first, then gives way to "I just got it last weekend" in the same place, clear of the model. */
function TopLine({ frame }: { frame: number }) {
  const got = BEATS.got - BEATS.reveal;
  return (
    <div className="absolute inset-x-safe-side top-[270px] text-paper-raised">
      {frame < got ? (
        <Slam at={BEATS.name - BEATS.reveal} className="reel-copy text-shout">
          my 3D
          <br />
          model
        </Slam>
      ) : (
        <Slam at={got} className="reel-copy text-headline">
          I just got it
          <br />
          last weekend
        </Slam>
      )}
    </div>
  );
}

function ProudModel() {
  const frame = useCurrentFrame();
  const whip = 1 - progress(frame, 0, 10);
  return (
    <div className="absolute inset-0 bg-night">
      <div className="absolute inset-0" style={{ transform: `scale(${1 + whip * 0.6})`, filter: `blur(${whip * 14}px)` }}>
        <TexturedRoom scan="moffett" width={REEL.width} height={REEL.height} cutaway={2.3} camera={{ azimuth: frame * 0.03, elevation: 0.95, distance: 84 }} />
      </div>
      <Sparkles />
      <TopLine frame={frame} />
      <div className="absolute inset-x-0 top-[1290px] text-center text-paper-raised">
        <Slam at={BEATS.woah - BEATS.reveal} className="reel-copy text-shout">
          woahhh
        </Slam>
      </div>
    </div>
  );
}

const cues = [
  cue(0, "tape", 0.7),
  cue(4, "tape", 0.6),
  cue(BEATS.reveal, "boom", 0.9),
  cue(BEATS.reveal, "whoosh", 0.6),
  cue(BEATS.name, "hit", 0.7),
  cue(BEATS.woah, "hit", 0.8),
  cue(BEATS.got, "hit", 0.6),
  cue(BEATS.proud, "hit", 0.5),
  cue(BEATS.proud + 6, "tape", 0.7),
];

export const DOLL_LENGTH = BEATS.length;

export function DollMeme() {
  return (
    <Paper tone="night">
      <Impact hits={[BEATS.reveal, BEATS.woah]} strength={22}>
        <Sequence durationInFrames={BEATS.reveal} layout="none">
          <Shot clip="zihao-caught">
            <TapeLines lines={["me showing", "everyone our", "library in 3D:"]} size="hook" at={-12} />
          </Shot>
        </Sequence>
        <Sequence from={BEATS.reveal} durationInFrames={BEATS.proud - BEATS.reveal} layout="none">
          <ProudModel />
        </Sequence>
        <Sequence from={BEATS.proud} layout="none">
          <Shot clip="zihao-caught" startFrom={26}>
            <TapeLines lines={["(it’s a library)"]} size="hook" at={4} />
          </Shot>
        </Sequence>
      </Impact>
      <Soundtrack bed="bed-pov" bedVolume={0.4} cues={cues} />
      <ExtraSounds cues={[{ at: BEATS.reveal + 2, sound: "a-sparkle", volume: 0.8 }, { at: BEATS.woah, sound: "a-sparkle", volume: 0.6 }]} />
      <Vignette strength={0.3} />
      <Grain strength={0.1} />
    </Paper>
  );
}
