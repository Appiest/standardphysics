import type { ModelLoopProgress } from "@/lib/model-loop-progress";
import type { ModelLoopEvent } from "@/types/contracts";

export function idleDetail(label: string): string {
  return `${label} tries moves that pass every check, one problem at a time. Nothing changes until you keep it.`;
}

export function workingDetail(label: string): string {
  return `${label} is choosing from moves that pass every check, then your shop is measured again.`;
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

/** Each move the model picked this turn as its own line, since one turn can move several pieces. */
export function turnMoves(turn: ModelLoopEvent): string[] {
  return turn.picked.length > 0 ? turn.picked.map(sentenceCase) : [NOTHING_PICKED];
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

function piecesMoved(count: number): string {
  return count === 1 ? "1 piece moves." : `${count} pieces move.`;
}

/** What moves, and when the run stopped short, the server's reason why. */
export function finishedDetail(finished: ModelLoopEvent, allCleared: boolean): string {
  const moved = finished.moves.length === 0 ? "The layout stays as it is." : piecesMoved(finished.moves.length);
  return allCleared || !finished.message ? moved : `${moved} ${finished.message}`;
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
