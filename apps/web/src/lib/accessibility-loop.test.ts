import { describe, expect, it } from "vitest";
import { accessibilityLoopRequest, LOOP_WORKERS, loopResultSentence } from "./accessibility-loop";

describe("accessibility loop", () => {
  it("runs all tests through a bounded parallel pool", () => {
    expect(accessibilityLoopRequest(5)).toMatchObject({ samples: 1000, max_workers: LOOP_WORKERS });
    expect(LOOP_WORKERS).toBeLessThan(1000);
  });
  it("starts Astra by default and schedules all 1,000 TypeSafe trials", () => {
    expect(accessibilityLoopRequest(7)).toEqual({
      base_revision: 7,
      samples: 1_000,
      max_workers: LOOP_WORKERS,
      router: "typesafe",
      refine_with_astra: true,
      typesafe_call_limit: 50_000,
      astra_rounds: 7,
      exhaustive_evaluations: 0,
    });
  });

  it("only calls a zero-violation result complete", () => {
    expect(loopResultSentence({
      converged: false, loop_cycles: 0, violating_trials: 0, ada_rule_violations: 0,
    })).toBe("Previous screening result. Start the loop to verify zero violations.");
    expect(loopResultSentence({
      converged: true, loop_cycles: 2, violating_trials: 0, ada_rule_violations: 0,
    })).toBe("Loop complete: zero violations after 2 batches of 1,000 tests.");
    expect(loopResultSentence({
      converged: false, loop_cycles: 3, violating_trials: 4, ada_rule_violations: 1,
    })).toBe("Loop stopped: 4 test violations and 1 ADA violation remain.");
  });
});
