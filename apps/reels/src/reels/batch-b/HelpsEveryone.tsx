import { Sequence, useCurrentFrame } from "remotion";
import { cue, Soundtrack } from "../../components/Cues";
import { FinePrint } from "../../components/FinePrint";
import { Impact } from "../../components/Impact";
import { Grain, Paper, Vignette } from "../../components/Paper";
import { progress, snap } from "../../lib/ease";
import { FileCues, Montage, SignOff, Stage, TapeLines, type Beat } from "./kit";

type Rule = { name: string; lines: string[]; source: string; clips: string[] };

const rules: Rule[] = [
  {
    name: "curb cuts",
    lines: ["strollers and suitcases", "roll right up them too"],
    source: "2010 ADA Standards §406",
    clips: ["b-stroller", "b-suitcase", "b-dolly"],
  },
  { name: "lever handles", lines: ["open it with your", "elbow, hands full"], source: "2010 ADA Standards §309.4 and §404.2.7", clips: ["b-lever"] },
  { name: "step-free doors", lines: ["anything on wheels", "just rolls in"], source: "2010 ADA Standards §402.2 and §405.2", clips: ["b-cart", "b-ramp"] },
  { name: "32-inch doors", lines: ["the couch", "fits too"], source: "2010 ADA Standards §404.2.3", clips: ["b-door-box"] },
];

const HOOK = 66;
const RULE = 96;
const STAMP_AT = 40;
const T = { outro: HOOK + rules.length * RULE, end: HOOK + rules.length * RULE + 120 } as const;

function ruleBeats(rule: Rule, index: number): Beat[] {
  const from = HOOK + index * RULE;
  const length = Math.ceil(RULE / rule.clips.length);
  return rule.clips.map((clip, clipIndex) => ({ clip, from: from + clipIndex * length, length: Math.min(length, RULE - clipIndex * length), startFrom: 30 }));
}

const beats: Beat[] = [
  { clip: "b-crossing", from: 0, length: HOOK, startFrom: 20 },
  ...rules.flatMap(ruleBeats),
  { clip: "b-wheelchair-smile", from: T.outro, length: T.end - T.outro, startFrom: 120 },
];

const stampFrames = rules.map((_, index) => HOOK + index * RULE + STAMP_AT);

const cues = [
  cue(0, "tape", 0.8),
  cue(5, "tape", 0.7),
  ...rules.flatMap((_, index) => [cue(HOOK + index * RULE, "whoosh", 0.35), cue(HOOK + index * RULE + 4, "tape", 0.7)]),
  cue(T.outro + 4, "tape", 0.7),
  cue(T.outro + 44, "pop", 0.5),
];

function Stamp() {
  const frame = useCurrentFrame();
  const land = progress(frame, STAMP_AT, 7, snap);
  if (frame < STAMP_AT) return null;
  return (
    <div className="absolute left-[80px] top-[1080px]" style={{ transform: `rotate(-7deg) scale(${1.7 - 0.7 * land})`, opacity: Math.min(1, land * 3), transformOrigin: "30% 50%" }}>
      <div className="border-[10px] border-ink bg-paper-raised/90 px-8 pb-2 shadow-[0_16px_40px_rgba(13,13,12,0.4)]">
        <span className="reel-copy figures text-shout">10/10</span>
      </div>
    </div>
  );
}

function RuleCard({ rule }: { rule: Rule }) {
  return (
    <>
      <Stage>
        <TapeLines lines={[rule.name]} at={4} exitAt={RULE - 6} size="headline" />
        <TapeLines lines={rule.lines} at={12} exitAt={RULE - 5} size="caption" className="mt-5" />
      </Stage>
      <Stamp />
      <div className="absolute inset-x-safe-side" style={{ top: 1360 }}>
        <FinePrint at={16} className="inline-block bg-paper-raised/90 px-4 py-2">
          {rule.source}
        </FinePrint>
      </div>
    </>
  );
}

export const HELPS_EVERYONE_DURATION = T.end;

export function HelpsEveryone() {
  return (
    <Paper tone="night">
      <Impact hits={stampFrames} strength={14}>
        <Montage beats={beats} />
        <Stage>
          <TapeLines lines={["accessibility rules", "that secretly", "help everyone"]} at={0} exitAt={HOOK - 6} stagger={4} />
        </Stage>
        {rules.map((rule, index) => (
          <Sequence key={rule.name} from={HOOK + index * RULE} durationInFrames={RULE} layout="none">
            <RuleCard rule={rule} />
          </Sequence>
        ))}
        <div className="absolute inset-x-safe-side top-[800px]">
          <TapeLines lines={["accessibility", "helps everyone"]} at={T.outro + 4} />
          <TapeLines lines={["like if you’d rate it", "10/10 too"]} at={T.outro + 26} size="caption" className="mt-6" />
        </div>
        <SignOff at={T.outro + 44} />
      </Impact>
      <Soundtrack bed="b-warm" bedVolume={0.42} cues={cues} />
      <FileCues cues={stampFrames.map((at) => ({ at, file: "b-stamp", volume: 0.9 }))} />
      <Vignette strength={0.3} />
      <Grain strength={0.08} />
    </Paper>
  );
}
