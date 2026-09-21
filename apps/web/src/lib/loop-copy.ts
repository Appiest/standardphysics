import { formatInches } from "@/lib/findings";
import { METERS_PER_INCH } from "@/lib/moves";
import type { LoopProgress } from "@/lib/loop-progress";
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
  const measurement = loopPass.kept
    ? `Re-measured and kept. The problems added up to ${before} short, and now ${after}.`
    : `Re-measured and turned down. It left the problems at ${after} short instead of ${before}.`;
  const rationale = moveRationale(loopPass.moves);
  return rationale ? `${measurement} ${rationale}` : measurement;
}

export function moveRationale(moves: LoopPass["moves"]): string | null {
  if (moves.length === 0) return null;
  const details = moves.map((move) => {
    const inches = Math.hypot(move.delta_translation.x, move.delta_translation.y) / METERS_PER_INCH;
    const turn = Math.round(Math.abs(move.delta_rotation_z_degrees));
    const parts = [inches >= 0.25 ? `moves ${formatInches(inches)}` : null, turn ? `turns ${turn}°` : null].filter(Boolean);
    return parts.join(" and ");
  }).filter(Boolean);
  return details.length > 0
    ? `Kept change: ${details.join("; ")}.`
    : "Kept change: no measurable translation or rotation.";
}

export function deciderSentence(decidedBy: string): string {
  return decidedBy === "typesafe" ? "TypeSafe chose each step." : "Our built-in policy chose each step.";
}

export function workingSentence(decidedBy: string | null): string {
  if (decidedBy === null) return "Getting the shop's measurements ready.";
  const decider = decidedBy === "typesafe" ? "TypeSafe" : "Our built-in policy";
  return `${decider} is choosing what to try, and then the shop gets measured again.`;
}

export function summary(result: LoopResult): string {
  const pieces = result.moves.length;
  if (pieces === 1) return "1 piece moves in the layout it kept.";
  if (pieces > 1) return `${pieces} pieces move in the layout it kept.`;
  const last = result.passes[result.passes.length - 1];
  return last?.problems === 0 ? "Nothing needs moving, because this layout has no problems left to fix." : "The search found no layout that could move without breaking something else.";
}

export function stoppedSentence(finishedPasses: number): string {
  if (finishedPasses === 0) return "Stopped before the first pass finished. Nothing was changed.";
  return finishedPasses === 1 ? "Stopped after 1 pass. Nothing was changed." : `Stopped after ${finishedPasses} passes. Nothing was changed.`;
}

/** The one sentence a screen reader hears each time the loop moves on. */
export function announcement(progress: LoopProgress): string {
  const latest = progress.passes[progress.passes.length - 1];
  const byPhase: Record<LoopProgress["phase"], string> = {
    idle: "",
    running: latest ? `Pass ${latest.number}: ${passTitle(latest)}` : "The improvement loop started.",
    finished: progress.result ? summary(progress.result) : "",
    stopped: stoppedSentence(progress.passes.length),
    failed: progress.error ?? "",
  };
  return byPhase[progress.phase];
}
