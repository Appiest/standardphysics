import { StepHeading } from "./StepHeading";
import type { Journey } from "@/types/contracts";

const EXPLAINED: Record<string, string> = {
  upload: "Keep Standard Physics open on your iPhone until the walk finishes uploading.",
  measuring: "This takes a few minutes. This page updates on its own when it's done.",
  failed: "Something in the walk didn't come through. Open Standard Physics on your iPhone and walk the shop again.",
};

/** While the server works: what's happening, and a line being drawn so the page is visibly alive. */
export function WaitingPanel({ journey }: { journey: Journey }) {
  const kind = journey.next_step.kind;
  return (
    <div className="flex flex-col gap-8" role="status">
      <StepHeading title={journey.next_step.title}>{EXPLAINED[kind] ?? EXPLAINED.measuring}</StepHeading>
      {kind !== "failed" && <DrawingLine />}
    </div>
  );
}

function DrawingLine() {
  return (
    <svg viewBox="0 0 240 8" preserveAspectRatio="none" className="h-2 w-full text-accent" aria-hidden>
      <line x1="0" y1="4" x2="240" y2="4" className="stroke-rule" strokeWidth="2" />
      <line x1="0" y1="4" x2="240" y2="4" stroke="currentColor" strokeWidth="2" strokeDasharray="60 180" className="motion-safe:animate-drafting-line" />
    </svg>
  );
}
