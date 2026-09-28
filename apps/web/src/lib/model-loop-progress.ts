import type { ModelLoopEvent } from "@/types/contracts";

export type ModelLoopProgress = {
  phase: "idle" | "running" | "finished" | "stopped" | "failed";
  /** Problems furniture can fix when the run began, from the `started` event. */
  startedWith: number | null;
  /** How many turns the run takes at most, from the `started` event. */
  turnsAtMost: number | null;
  /** The problems still open, which the next turn works on: from `started`, then each turn. */
  workingOn: string[];
  turns: ModelLoopEvent[];
  finished: ModelLoopEvent | null;
  error: string | null;
};

export type ModelLoopAction =
  | ModelLoopEvent
  | { kind: "start" }
  | { kind: "stop" }
  | { kind: "closed" }
  | { kind: "refused"; error: string };

export const NOT_STARTED: ModelLoopProgress = { phase: "idle", startedWith: null, turnsAtMost: null, workingOn: [], turns: [], finished: null, error: null };

export const CONNECTION_LOST = "Lost the connection before the fix finished. Nothing was changed, so you can start it again.";

type Handler = (progress: ModelLoopProgress, action: ModelLoopAction) => ModelLoopProgress;

/** Lines still in flight after Stop, or a close after the last event, change nothing. */
const whileRunning = (progress: ModelLoopProgress, next: ModelLoopProgress) => (progress.phase === "running" ? next : progress);

const eventOf = (action: ModelLoopAction) => action as ModelLoopEvent;

const HANDLERS: Record<ModelLoopAction["kind"], Handler> = {
  start: () => ({ ...NOT_STARTED, phase: "running" }),
  started: (progress, action) => whileRunning(progress, {
    ...progress, startedWith: eventOf(action).fixable_left ?? null, turnsAtMost: eventOf(action).turns_at_most ?? null, workingOn: eventOf(action).working_on,
  }),
  turn: (progress, action) => whileRunning(progress, { ...progress, turns: [...progress.turns, eventOf(action)], workingOn: eventOf(action).working_on }),
  finished: (progress, action) => whileRunning(progress, { ...progress, phase: "finished", finished: eventOf(action) }),
  failed: (progress, action) => whileRunning(progress, { ...progress, phase: "failed", error: eventOf(action).message }),
  stop: (progress) => whileRunning(progress, { ...progress, phase: "stopped" }),
  closed: (progress) => whileRunning(progress, { ...progress, phase: "failed", error: CONNECTION_LOST }),
  refused: (progress, action) => whileRunning(progress, { ...progress, phase: "failed", error: (action as { error: string }).error }),
};

export function advanceModelLoop(progress: ModelLoopProgress, action: ModelLoopAction): ModelLoopProgress {
  return HANDLERS[action.kind](progress, action);
}
