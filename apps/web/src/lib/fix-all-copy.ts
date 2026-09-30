import type { ModelLoopProgress } from "@/lib/model-loop-progress";
import type { ModelLoopEvent } from "@/types/contracts";

/** Which turn is running, out of the most the run can take. */
export function turnInProgress(turn: number, atMost: number | null): string {
  return atMost ? `Turn ${turn} of up to ${atMost}` : `Turn ${turn}`;
}

/** Seconds spent on this turn so far, as a clock: 0:07, 1:12. */
export function turnClock(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** The open problems to name, and how many more there are beyond them. */
export function namedProblems(workingOn: string[], shown = 3): { named: string[]; more: number } {
  return { named: workingOn.slice(0, shown), more: Math.max(0, workingOn.length - shown) };
}

/** Problems still open: the latest turn's count, or the count the run started with. */
export function problemsLeft(progress: ModelLoopProgress): number | null {
  const latest = progress.turns[progress.turns.length - 1];
  return latest?.fixable_left ?? progress.startedWith;
}

export function problemsLeftLabel(count: number): string {
  return count === 1 ? "problem left" : "problems left";
}

const sentenceCase = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

const NOTHING_PICKED = "Picked nothing it could use";

/** A turn is named by the move the model picked, in the owner's words. */
export function turnTitle(turn: ModelLoopEvent): string {
  return turn.picked.length > 0 ? sentenceCase(turn.picked.join("; ")) : NOTHING_PICKED;
}

export type TurnLine = { text: string; construction: boolean };

/** Each move the model picked this turn as its own line, since one turn can move several pieces; a slid built-in is construction. */
export function turnLines(turn: ModelLoopEvent): TurnLine[] {
  if (turn.picked.length === 0) return [{ text: NOTHING_PICKED, construction: false }];
  const builtIn = new Set(turn.construction);
  return turn.picked.map((pick) => ({ text: sentenceCase(pick), construction: builtIn.has(pick) }));
}

export function isAllCleared(finished: ModelLoopEvent | null, started: number | null): boolean {
  return Boolean(finished && started && finished.fixable_left === 0 && finished.moves.length > 0);
}

/** The finished run in one line, counted against the problems it started with. */
export function finishedHeadline(finished: ModelLoopEvent, started: number): string {
  const left = finished.fixable_left ?? started;
  if (started === 0) return "Nothing here can be fixed by moving furniture";
  if (left === 0) return started === 1 ? "The problem is fixed" : `All ${started} problems fixed`;
  if (left < started) return `Fixed ${started - left} of ${started} problems`;
  return "No furniture move fixed a problem";
}

/** The button that puts the proposed moves on the plan, counted so the owner knows what to look for. */
export function showMovesLabel(count: number): string {
  return count === 1 ? "Show this move on the plan" : `Show these ${count} moves on the plan`;
}

export function stoppedSentence(turns: number): string {
  if (turns === 0) return "Stopped before the first move. Nothing was changed.";
  return turns === 1 ? "Stopped after 1 move. Nothing was changed." : `Stopped after ${turns} moves. Nothing was changed.`;
}

/** The one sentence a screen reader hears each time the run moves on. */
export function fixAllAnnouncement(progress: ModelLoopProgress): string {
  const latest = progress.turns[progress.turns.length - 1];
  const left = problemsLeft(progress);
  const byPhase: Record<ModelLoopProgress["phase"], string> = {
    idle: "",
    running: latest && left !== null ? `${turnTitle(latest)}. ${left} ${problemsLeftLabel(left)}.` : "Fixing every layout problem.",
    finished: progress.finished ? finishedHeadline(progress.finished, progress.startedWith ?? 0) : "",
    stopped: stoppedSentence(progress.turns.length),
    failed: progress.error ?? "",
  };
  return byPhase[progress.phase];
}
