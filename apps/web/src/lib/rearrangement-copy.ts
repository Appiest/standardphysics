import type { RearrangementStatus } from "@/types/contracts";
import { ApiRefusal } from "@/lib/layout-client";

type Phase = NonNullable<RearrangementStatus["phase"]>;

const PHASE_SENTENCE: Record<Phase, string> = {
  waiting: "Asking the model for a layout",
  asking_model: "Asking the model for a layout",
  starting_model: "Starting the model. This can take a few minutes on the first request.",
  checking: "Checking the layout against every rule",
  trying_again: "The last idea did not pass the checks, so the model is trying again.",
};

const RETRY_SENTENCE: Record<string, string> = {
  collided: "The last idea bumped one piece into another, so the model is trying again.",
  new_problem: "The last idea caused a new problem, so the model is trying again.",
  noise: "The last idea changed too little to measure, so the model is trying again.",
  nothing_changed: "The last idea did not improve a problem, so the model is trying again.",
  left_the_floor: "The last idea pushed a piece off the floor, so the model is trying again.",
  pinned: "The last idea moved an uncertain piece, so the model is trying again.",
  blocked_keep_clear: "The last idea blocked a clear space, so the model is trying again.",
};

export function phaseSentence(phase: RearrangementStatus["phase"], reason: string | null = null): string {
  if (phase === "trying_again" && reason) return RETRY_SENTENCE[reason] ?? PHASE_SENTENCE.trying_again;
  return PHASE_SENTENCE[phase ?? "waiting"];
}

export function isWorking(status: RearrangementStatus | null): boolean {
  return status?.state === "queued" || status?.state === "running";
}

export function refusalSentence(error: unknown): string {
  if (error instanceof ApiRefusal && error.status === 409) {
    return "Someone saved a newer layout. Reload the page to ask about that one.";
  }
  if (error instanceof ApiRefusal && error.status === 503) return error.error;
  return "We couldn't reach the server just now. Try again.";
}
