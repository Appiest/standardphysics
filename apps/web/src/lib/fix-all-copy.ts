import type { ModelLoopProgress } from "@/lib/model-loop-progress";
import type { ModelLoopEvent } from "@/types/contracts";

export function idleDetail(label: string): string {
  return `${label} works through the whole room, choosing only moves that pass every check. Nothing changes until you keep it.`;
}

/** Which turn is running, out of the most the run can take. */
export function turnInProgress(turn: number, atMost: number | null): string {
  return atMost ? `Turn ${turn} of up to ${atMost}` : `Turn ${turn}`;
}

/** Seconds spent on this turn so far, as a clock: 0:07, 1:12. */
export function turnClock(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** What happens during a turn, which is why it takes a while. */
export function turnWork(label: string): string {
  return `${label} measures every move that fits your shop against the ADA rules, then picks one.`;
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

/** How many pieces move, and how many of them are built in and need a contractor. */
function piecesMoved(count: number, builtIns: number): string {
  if (count === 1) return builtIns === 1 ? "1 built-in piece moves. A contractor has to move it." : "1 piece moves.";
  if (builtIns === 0) return `${count} pieces move.`;
  const contractor = builtIns === 1 ? "One is built in, so a contractor has to move it." : `${builtIns} are built in, so a contractor has to move them.`;
  return `${count} pieces move. ${contractor}`;
}

/** What moves, and when the run stopped short, the server's reason why. */
export function finishedDetail(finished: ModelLoopEvent, allCleared: boolean): string {
  const moved = finished.moves.length === 0 ? "The layout stays as it is." : piecesMoved(finished.moves.length, finished.built_ins.length);
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
