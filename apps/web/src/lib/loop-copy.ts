import { formatInches } from "@/lib/findings";
import type { LoopPass, LoopResult } from "@/types/contracts";

type Action = NonNullable<LoopPass["action"]>;

const ACTION_TITLE: Record<Action, string> = {
  FIX: "Moved furniture",
  RESCAN_AREA: "Asked for another scan",
  ASK_OWNER: "Has a question for you",
  ESCALATE: "Handed to a professional",
  DONE: "Finished",
};

export function passTitle(loopPass: LoopPass): string {
  if (!loopPass.action) return "Stopped, because the next step didn't check out";
  if (loopPass.action === "FIX" && !loopPass.kept) return "Tried moving furniture";
  return ACTION_TITLE[loopPass.action];
}

export function passOutcome(loopPass: LoopPass): string {
  if (loopPass.kept === null || loopPass.inches_short_before === null || loopPass.inches_short_after === null) {
    return loopPass.question ?? loopPass.message;
  }
  const before = formatInches(loopPass.inches_short_before);
  const after = formatInches(loopPass.inches_short_after);
  return loopPass.kept
    ? `Re-measured and kept. The problems added up to ${before} short, and now ${after}.`
    : `Re-measured and turned down. It left the problems at ${after} short instead of ${before}.`;
}

export function deciderSentence(result: LoopResult): string {
  return result.decided_by === "typesafe" ? "TypeSafe chose each step." : "Our built-in policy chose each step.";
}

export function summary(result: LoopResult): string {
  const pieces = result.moves.length;
  if (pieces === 0) return "Nothing could move without breaking something else.";
  return pieces === 1 ? "1 piece moves in the layout it kept." : `${pieces} pieces move in the layout it kept.`;
}
