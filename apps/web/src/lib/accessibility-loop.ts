import type { SimulationRequest, SimulationResult } from "@/types/contracts";

export const LOOP_TRIALS = 1_000;
export const LOOP_WORKERS = 16;

export function accessibilityLoopRequest(baseRevision: number): SimulationRequest {
  return {
    base_revision: baseRevision,
    samples: LOOP_TRIALS,
    max_workers: LOOP_WORKERS,
    router: "typesafe",
    refine_with_astra: true,
    typesafe_call_limit: 50_000,
    astra_rounds: 7,
    exhaustive_evaluations: 0,
  };
}

type LoopResult = Pick<
  SimulationResult,
  "converged" | "loop_cycles" | "violating_trials" | "ada_rule_violations"
>;

export function loopResultSentence(result: LoopResult): string {
  if (result.loop_cycles === 0) {
    return "Previous screening result. Start the loop to verify zero violations.";
  }
  if (result.converged) {
    return `Loop complete: zero violations after ${result.loop_cycles} ${result.loop_cycles === 1 ? "batch" : "batches"} of ${LOOP_TRIALS.toLocaleString("en-US")} tests.`;
  }
  return `Loop stopped: ${result.violating_trials} test ${result.violating_trials === 1 ? "violation" : "violations"} and ${result.ada_rule_violations} ADA ${result.ada_rule_violations === 1 ? "violation" : "violations"} remain.`;
}
