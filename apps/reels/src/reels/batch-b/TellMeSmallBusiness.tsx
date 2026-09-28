import { cue, Soundtrack } from "../../components/Cues";
import { Grain, Paper, Vignette } from "../../components/Paper";
import { Montage, SignOff, Stage, TapeLines, type Beat } from "./kit";

type Answer = { clip: string; lines: string[]; startFrom?: number };

const answers: Answer[] = [
  { clip: "b-cake", lines: ["“we’re a small team”", "(it’s just me)"] },
  { clip: "b-barista", lines: ["lunch is a muffin", "over the register"] },
  { clip: "b-tape", lines: ["I know my counter’s", "height by heart"], startFrom: 60 },
  { clip: "b-paperwork", lines: ["did payroll and", "fixed the sink", "on one Tuesday"] },
  { clip: "b-dolly", lines: ["measured the door", "before ordering", "the new fridge"] },
];

const HOOK = 72;
const BEAT = 68;
const T = { outro: HOOK + answers.length * BEAT, end: HOOK + answers.length * BEAT + 110 } as const;

const beats: Beat[] = [
  { clip: "b-florist-tablet", from: 0, length: HOOK },
  ...answers.map((answer, index) => ({ clip: answer.clip, from: HOOK + index * BEAT, length: BEAT, startFrom: answer.startFrom })),
  { clip: "b-florist-hand", from: T.outro, length: T.end - T.outro },
];

const cues = [
  cue(0, "tape", 0.8),
  cue(5, "tape", 0.7),
  ...answers.flatMap((_, index) => [cue(HOOK + index * BEAT, "hit", 0.35), cue(HOOK + index * BEAT + 5, "tape", 0.55)]),
  cue(T.outro, "whoosh", 0.4),
  cue(T.outro + 4, "tape", 0.7),
  cue(T.outro + 40, "pop", 0.5),
];

function Answers() {
  return (
    <>
      {answers.map((answer, index) => (
        <Stage key={answer.clip}>
          <TapeLines lines={answer.lines} at={HOOK + index * BEAT + 5} exitAt={HOOK + (index + 1) * BEAT - 5} />
        </Stage>
      ))}
    </>
  );
}

export const TELL_ME_DURATION = T.end;

export function TellMeSmallBusiness() {
  return (
    <Paper tone="night">
      <Montage beats={beats} />
      <Stage>
        <TapeLines lines={["tell me you run", "a small business", "without telling me"]} at={0} exitAt={HOOK - 6} stagger={5} />
      </Stage>
      <Answers />
      <Stage>
        <TapeLines lines={["like if this", "is you"]} at={T.outro + 4} size="headline" />
        <TapeLines lines={["(or your mom)"]} at={T.outro + 24} size="caption" className="mt-4" />
      </Stage>
      <SignOff at={T.outro + 40} />
      <Soundtrack bed="bed-pov" bedVolume={0.35} cues={cues} />
      <Vignette strength={0.3} />
      <Grain strength={0.08} />
    </Paper>
  );
}
