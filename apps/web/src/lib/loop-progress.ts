import type { LoopEvent, LoopPass, LoopResult } from "@/types/contracts";

export type LoopProgress = {
  phase: "idle" | "running" | "finished" | "stopped" | "failed";
  decidedBy: string | null;
  passes: LoopPass[];
  result: LoopResult | null;
  error: string | null;
};

export type LoopAction =
  | LoopEvent
  | { kind: "start" }
  | { kind: "stop" }
  | { kind: "closed" }
  | { kind: "refused"; error: string };

export const NOT_STARTED: LoopProgress = { phase: "idle", decidedBy: null, passes: [], result: null, error: null };

export const CONNECTION_LOST = "Lost the connection before the loop finished. Nothing was changed, so you can start it again.";

type Handlers = {
  [Kind in LoopAction["kind"]]: (progress: LoopProgress, action: Extract<LoopAction, { kind: Kind }>) => LoopProgress;
};

/** Lines still in flight after Stop, or a close after the last event, change nothing. */
const whileRunning = (progress: LoopProgress, next: LoopProgress) => (progress.phase === "running" ? next : progress);

const HANDLERS: Handlers = {
  start: () => ({ ...NOT_STARTED, phase: "running" }),
  started: (progress, event) => whileRunning(progress, { ...progress, decidedBy: event.decided_by }),
  pass: (progress, event) => whileRunning(progress, { ...progress, passes: [...progress.passes, event.loop_pass] }),
  finished: (progress, event) =>
    whileRunning(progress, { ...progress, phase: "finished", passes: event.result.passes, result: event.result }),
  failed: (progress, event) => whileRunning(progress, { ...progress, phase: "failed", error: event.error }),
  stop: (progress) => whileRunning(progress, { ...progress, phase: "stopped" }),
  closed: (progress) => whileRunning(progress, { ...progress, phase: "failed", error: CONNECTION_LOST }),
  refused: (progress, action) => whileRunning(progress, { ...progress, phase: "failed", error: action.error }),
};

export function advanceLoop(progress: LoopProgress, action: LoopAction): LoopProgress {
  const handle = HANDLERS[action.kind] as (progress: LoopProgress, action: LoopAction) => LoopProgress;
  return handle(progress, action);
}
