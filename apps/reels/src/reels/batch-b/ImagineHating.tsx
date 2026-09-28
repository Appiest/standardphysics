import { cue, Soundtrack } from "../../components/Cues";
import { Grain, Paper, Vignette } from "../../components/Paper";
import { Montage, SignOff, Stage, TapeLines, type Beat } from "./kit";

type Person = { clip: string; lines: [string, string]; startFrom?: number };

const people: Person[] = [
  { clip: "b-serve", lines: ["the barista who", "knows your order"] },
  { clip: "b-florist-hand", lines: ["the florist who", "adds an extra stem"], startFrom: 60 },
  { clip: "b-bike", lines: ["the bike guy who", "fixed it for free"], startFrom: 40 },
  { clip: "b-pastry", lines: ["the baker who saved", "you a croissant"] },
  { clip: "b-tailor", lines: ["the tailor who", "hemmed it by lunch"] },
];

const BEAT = 66;
const T = { outro: people.length * BEAT, like: people.length * BEAT + 40, end: people.length * BEAT + 130 } as const;

const beats: Beat[] = [
  ...people.map((person, index) => ({ clip: person.clip, from: index * BEAT, length: BEAT, startFrom: person.startFrom })),
  { clip: "b-florist", from: T.outro, length: T.end - T.outro, startFrom: 90 },
];

const cues = [
  cue(0, "tape", 0.8),
  ...people.flatMap((_, index) => [cue(index * BEAT, "tick", 0.5), cue(index * BEAT + 6, "tape", 0.55)]),
  cue(T.outro, "whoosh", 0.4),
  cue(T.outro + 4, "hit", 0.5),
  cue(T.like, "tape", 0.6),
  cue(T.like + 34, "pop", 0.5),
];

function Subjects() {
  return (
    <>
      {people.map((person, index) => (
        <Stage key={person.clip}>
          <TapeLines lines={person.lines} at={index * BEAT + 6} exitAt={(index + 1) * BEAT - 5} className="mt-[124px]" />
        </Stage>
      ))}
    </>
  );
}

export const IMAGINE_HATING_DURATION = T.end;

export function ImagineHating() {
  return (
    <Paper tone="night">
      <Montage beats={beats} />
      <Stage>
        <TapeLines lines={["imagine hating"]} at={0} exitAt={T.outro - 4} size="caption" />
      </Stage>
      <Subjects />
      <Stage>
        <TapeLines lines={["couldn’t be us."]} at={T.outro + 4} size="headline" />
        <TapeLines lines={["like if you love", "your local shops"]} at={T.like} className="mt-6" />
      </Stage>
      <SignOff at={T.like + 34} />
      <Soundtrack bed="b-warm" bedVolume={0.45} cues={cues} />
      <Vignette strength={0.3} />
      <Grain strength={0.08} />
    </Paper>
  );
}
