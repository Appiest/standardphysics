import { Sequence, useCurrentFrame } from "remotion";
import { cue, Soundtrack } from "../../../components/Cues";
import { FinePrint } from "../../../components/FinePrint";
import { Footage } from "../../../components/Footage";
import { Impact } from "../../../components/Impact";
import { Grain, Paper, Vignette } from "../../../components/Paper";
import { TapeStack } from "../../../components/TapeStack";
import { RevealLines } from "../../../components/Type";
import { drawn, progress, snap } from "../../../lib/ease";
import { PLATE, plates, type PlateName } from "./diagrams";

type Fact = { plate: PlateName; lines: string[]; section: string };

const HOOK: Fact = {
  plate: "knob",
  lines: ["Round doorknobs", "are technically", "an ADA", "violation."],
  section: "2010 ADA Standards 309.4 and 404.2.7: door hardware can’t need tight grasping or a twist of the wrist",
};

const FACTS: Fact[] = [
  { plate: "counter", lines: ["So is a sales", "counter with no", "part 36 inches", "or lower."], section: "2010 ADA Standards 904.4.1" },
  { plate: "doorway", lines: ["So is a doorway", "under 32 inches", "wide."], section: "2010 ADA Standards 404.2.3" },
  { plate: "ramp", lines: ["So is a ramp", "that rises more", "than an inch", "per foot."], section: "2010 ADA Standards 405.2" },
  { plate: "mirror", lines: ["So is a restroom", "mirror whose", "bottom edge sits", "above 40 inches."], section: "2010 ADA Standards 603.3" },
  { plate: "threshold", lines: ["So is a door", "threshold over", "half an inch", "high."], section: "2010 ADA Standards 404.2.5" },
];

const CARD = { hook: 66, fact: 50, drawFrames: 30 } as const;
const outroFrom = CARD.hook + FACTS.length * CARD.fact;
const OUTRO = 96;

export const DOORKNOB_FACTS_DURATION = outroFrom + OUTRO;

const cardStarts = FACTS.map((_, index) => CARD.hook + index * CARD.fact);

const cues = [
  cue(0, "hit", 0.8),
  cue(CARD.hook - 26, "hit", 0.6),
  ...cardStarts.flatMap((at) => [cue(at, "whoosh", 0.45), cue(at + CARD.drawFrames - 6, "tick", 0.6)]),
  cue(outroFrom, "whoosh-down", 0.5),
  cue(outroFrom + 8, "tape", 0.7),
  cue(outroFrom + 16, "tape", 0.6),
];

function Plate({ name, frame }: { name: PlateName; frame: number }) {
  const drawnShare = progress(frame, 2, CARD.drawFrames, drawn);
  return (
    <svg viewBox={`0 0 ${PLATE.width} ${PLATE.height}`} className="absolute left-[90px] top-[860px] h-[760px] w-[900px] overflow-visible">
      {plates[name](drawnShare)}
    </svg>
  );
}

function FactCard({ fact, index, big }: { fact: Fact; index?: number; big?: boolean }) {
  const frame = useCurrentFrame();
  const slide = progress(frame, 0, 8, snap);
  return (
    <div className="absolute inset-0" style={{ transform: `translateX(${(1 - slide) * 120}px)`, opacity: slide }}>
      <div className="absolute inset-x-safe-side top-safe-top">
        {index !== undefined && <p className="reel-caption figures text-caption text-ink-muted">{index + 2} of 6</p>}
        <RevealLines lines={fact.lines} at={0} stagger={2} className={`reel-copy block ${big ? "text-c-hook" : "text-title"}`} />
        {big && <p className="reel-caption mt-6 text-caption text-ink-muted">on doors in shops, cafés and other public places</p>}
      </div>
      <Plate name={fact.plate} frame={frame} />
      <FinePrint at={10} className="absolute inset-x-safe-side top-[1640px]">
        {fact.section}
      </FinePrint>
    </div>
  );
}

function Outro() {
  const frame = useCurrentFrame();
  return (
    <div className="absolute inset-0 overflow-hidden bg-night">
      <div className="absolute inset-0" style={{ transform: `scale(${1.08 - progress(frame, 0, 30, drawn) * 0.08})` }}>
        <Footage clip="stock-c-open-sign" startFrom={20} />
      </div>
      <TapeStack lines={["tap like if you’ve walked", "past all six of these"]} at={8} />
    </div>
  );
}

/** A rapid-fire list of everyday things that break the 2010 ADA Standards, each drawn as an ink plate with its limit lit up. */
export function DoorknobFacts() {
  return (
    <Paper>
      <Impact hits={[0, ...cardStarts, outroFrom]} strength={12}>
        <Sequence durationInFrames={CARD.hook} layout="none">
          <FactCard fact={HOOK} big />
        </Sequence>
        {FACTS.map((fact, index) => (
          <Sequence key={fact.plate} from={cardStarts[index]} durationInFrames={CARD.fact} layout="none">
            <FactCard fact={fact} index={index} />
          </Sequence>
        ))}
        <Sequence from={outroFrom} layout="none">
          <Outro />
        </Sequence>
      </Impact>
      <Soundtrack bed="c-bed-facts" bedVolume={0.4} cues={cues} />
      <Vignette strength={0.25} />
      <Grain strength={0.1} />
    </Paper>
  );
}
