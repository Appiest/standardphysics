import { Sequence } from "remotion";
import { cue, Soundtrack } from "../../components/Cues";
import { EndCard } from "../../components/EndCard";
import { Grain, Paper, Vignette } from "../../components/Paper";
import { PaperWipe } from "../../components/PaperWipe";
import { Shot } from "../../components/Shot";
import { clackCues, ExtraSounds, TapeLines, Typed } from "./kit";
import { ModelSweep } from "./scenes";

const CUTS = { us: 46, ceiling: 108, dinner: 168, phone: 228, model: 298, end: 386, length: 450 } as const;
const PROMISE = "“AI will replace you.”";

function ThePromise() {
  return (
    <Paper>
      <div className="absolute inset-x-safe-side top-[520px]">
        <Typed text={PROMISE} at={-4} className="reel-copy text-shout" />
      </div>
      <TapeLines lines={["the internet, constantly"]} at={16} top={1100} />
    </Paper>
  );
}

const cues = [
  cue(16, "tape", 0.6),
  ...[CUTS.us, CUTS.ceiling, CUTS.dinner, CUTS.phone].map((at) => cue(at, "hit", 0.55)),
  cue(CUTS.us, "m-record-stop", 1),
  cue(CUTS.us + 4, "tape", 0.6),
  cue(CUTS.us + 8, "tape", 0.5),
  cue(CUTS.ceiling + 6, "tape", 0.6),
  cue(CUTS.dinner + 6, "tape", 0.6),
  cue(CUTS.phone + 6, "tape", 0.6),
  cue(CUTS.phone + 10, "tape", 0.5),
  cue(CUTS.phone + 44, "riser", 0.7),
  cue(CUTS.model, "boom", 0.9),
  cue(CUTS.model + 2, "scan", 0.8),
  cue(CUTS.model + 24, "m-vine-boom", 0.9),
  cue(CUTS.model + 26, "tape", 0.7),
  cue(CUTS.model + 30, "tape", 0.6),
  cue(CUTS.end, "whoosh-down", 0.6),
  cue(CUTS.end + 10, "hit", 0.5),
  cue(CUTS.end + 24, "pop", 0.5),
];

export const AI_REPLACE_LENGTH = CUTS.length;

export function AiReplaceUs() {
  return (
    <Paper tone="night">
      <Sequence durationInFrames={CUTS.us} layout="none">
        <ThePromise />
      </Sequence>
      <Sequence from={CUTS.us} durationInFrames={CUTS.ceiling - CUTS.us} layout="none">
        <Shot clip="boris-studiers" startFrom={20}>
          <TapeLines lines={["meanwhile, us at 7pm", "on a thursday:"]} at={2} />
        </Shot>
      </Sequence>
      <Sequence from={CUTS.ceiling} durationInFrames={CUTS.dinner - CUTS.ceiling} layout="none">
        <Shot clip="zihao-ceiling" startFrom={40}>
          <TapeLines lines={["holding a phone", "up to the ceiling"]} at={4} />
        </Shot>
      </Sequence>
      <Sequence from={CUTS.dinner} durationInFrames={CUTS.phone - CUTS.dinner} layout="none">
        <Shot clip="zihao-glass" startFrom={30}>
          <TapeLines lines={["during everyone’s dinner"]} at={4} />
        </Shot>
      </Sequence>
      <Sequence from={CUTS.phone} durationInFrames={CUTS.model - CUTS.phone} layout="none">
        <Shot clip="phone-chair">
          <TapeLines lines={["so the AI can check", "if a wheelchair fits"]} at={4} />
        </Shot>
      </Sequence>
      <Sequence from={CUTS.model} durationInFrames={CUTS.end - CUTS.model + 20} layout="none">
        <ModelSweep>
          <TapeLines lines={["replaced? we walk", "more than ever"]} size="hook" at={24} />
        </ModelSweep>
      </Sequence>
      <Sequence from={CUTS.end} layout="none">
        <PaperWipe>
          <EndCard at={10} />
        </PaperWipe>
      </Sequence>
      <Soundtrack bed="bed-pov" bedVolume={0.38} cues={cues} />
      <ExtraSounds cues={clackCues(0, PROMISE.length - 8)} />
      <Vignette strength={0.3} />
      <Grain strength={0.1} />
    </Paper>
  );
}
