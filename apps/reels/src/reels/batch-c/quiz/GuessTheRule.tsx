import { Sequence, useCurrentFrame } from "remotion";
import { cue, repeatCue, Soundtrack } from "../../../components/Cues";
import { Impact } from "../../../components/Impact";
import { Grain, Paper, Vignette } from "../../../components/Paper";
import { RevealLines } from "../../../components/Type";
import { progress, snap } from "../../../lib/ease";
import { OneShots, shot } from "../Sounds";
import { ROUND, Round, type Question } from "./Round";

const QUESTIONS: Question[] = [
  {
    lines: ["How tall can a store’s", "checkout counter be?"],
    options: ["30 in", "36 in", "42 in"],
    answer: 1,
    rule: "At least 36 inches of the counter has to be 36 inches high or lower.",
    source: "2010 ADA Standards 904.4.1",
  },
  {
    lines: ["How wide does a", "doorway need to be?"],
    options: ["28 in", "32 in", "40 in"],
    answer: 1,
    rule: "32 inches clear, measured with the door open 90 degrees.",
    source: "2010 ADA Standards 404.2.3",
  },
  {
    lines: ["How much can a ramp", "rise per foot?"],
    options: ["1 in", "2 in", "3 in"],
    answer: 0,
    rule: "A slope of 1:12, so one inch up for every foot along.",
    source: "2010 ADA Standards 405.2",
  },
];

const HOOK = 48;
const roundStarts = QUESTIONS.map((_, index) => HOOK + index * ROUND.length);
const outroFrom = HOOK + QUESTIONS.length * ROUND.length;
const OUTRO = 84;

export const GUESS_THE_RULE_DURATION = outroFrom + OUTRO;

const cues = [
  cue(0, "hit", 0.7),
  ...roundStarts.flatMap((at) => [
    cue(at, "whoosh", 0.5),
    ...[0, 1, 2].map((option) => cue(at + ROUND.optionsAt + option * 5, "pop", 0.35)),
    ...repeatCue(at + ROUND.countdownAt, at + ROUND.revealAt - 20, 20, "tick", 0.8),
  ]),
  cue(outroFrom, "whoosh-down", 0.5),
  cue(outroFrom + 10, "hit", 0.5),
];

const shots = roundStarts.map((at) => shot(at + ROUND.revealAt, "c-chime", 0.8));

function Hook() {
  const frame = useCurrentFrame();
  const slam = progress(frame, 0, 8, snap);
  return (
    <div className="absolute inset-x-safe-side top-[600px]" style={{ transform: `scale(${1.3 - 0.3 * slam})`, opacity: slam, transformOrigin: "0 50%" }}>
      <p className="reel-copy text-poster">Guess</p>
      <p className="reel-copy text-headline">the ADA rule.</p>
      <p className="reel-caption mt-8 text-title text-ink-muted">You get three seconds a round.</p>
    </div>
  );
}

function Outro() {
  return (
    <div className="absolute inset-x-safe-side top-[640px]">
      <RevealLines lines={["How many did", "you get?"]} at={4} className="reel-copy block text-headline" />
      <RevealLines lines={["Tap like if you", "got all three."]} at={22} className="reel-caption mt-10 block text-title text-ink-muted" />
    </div>
  );
}

/** A three-round quiz on everyday ADA numbers, with a countdown to play along with and a lit reveal for each answer. */
export function GuessTheRule() {
  const reveals = roundStarts.map((at) => at + ROUND.revealAt);
  return (
    <Paper>
      <Impact hits={[0, ...reveals, outroFrom]} strength={12}>
        <Sequence durationInFrames={HOOK} layout="none">
          <Hook />
        </Sequence>
        {QUESTIONS.map((question, index) => (
          <Sequence key={question.source} from={roundStarts[index]} durationInFrames={ROUND.length} layout="none">
            <Round question={question} number={index + 1} />
          </Sequence>
        ))}
        <Sequence from={outroFrom} layout="none">
          <Outro />
        </Sequence>
      </Impact>
      <Soundtrack bed="c-bed-quiz" bedVolume={0.35} cues={cues} />
      <OneShots shots={shots} />
      <Vignette strength={0.25} />
      <Grain strength={0.1} />
    </Paper>
  );
}
