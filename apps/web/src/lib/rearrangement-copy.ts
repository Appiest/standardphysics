import type { RearrangementStatus } from "@/types/contracts";
import { ApiRefusal } from "@/lib/layout-client";

type Phase = NonNullable<RearrangementStatus["phase"]>;

const PHASE_SENTENCE: Record<Phase, string> = {
  waiting: "Asking the model for a layout",
  asking_model: "Asking the model for a layout",
  starting_model: "Starting the model. This can take a few minutes on the first request.",
  checking: "Checking the model's layouts against every rule",
};

export function phaseSentence(phase: RearrangementStatus["phase"]): string {
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
