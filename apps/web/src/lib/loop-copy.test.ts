import { describe, expect, it } from "vitest";
import type { LoopPass, LoopResult } from "@/types/contracts";
import { announcement, deciderSentence, moveRationale, passOutcome, passTitle, stoppedSentence, summary, workingSentence } from "./loop-copy";
import type { LoopProgress } from "./loop-progress";

const base: LoopPass = {
  number: 1, action: "FIX", problems: 2, questions: 5, message: "Move the two display cases 5.5 inches apart.",
  kept: true, inches_short_before: 16, inches_short_after: 11, moves: [], question: null,
};

describe("loop copy", () => {
  it("names the router's choice and what the re-measure decided", () => {
    expect(passTitle(base)).toBe("Moved furniture");
    expect(passOutcome(base)).toBe("Re-measured and kept. The problems added up to 16 in short, and now 11 in.");
    expect(passOutcome({ ...base, kept: false, inches_short_after: 16 })).toContain("turned down");
  });

  it("keeps the accepted move distance and turn beside the re-measurement", () => {
    const moved = { ...base, moves: [{ node_id: "case", delta_translation: { x: 0.127, y: 0, z: 0 }, delta_rotation_z_degrees: 15 }] };
    expect(passOutcome(moved)).toContain("Kept change: moves 5 in and turns 15°.");
    expect(moveRationale([])).toBeNull();
  });

  it("falls back to the loop's own sentence when no layout was tried", () => {
    const failed = { ...base, kept: null, inches_short_before: null, inches_short_after: null, message: "We couldn't find an arrangement that works." };
    expect(passOutcome(failed)).toBe("We couldn't find an arrangement that works.");
    expect(passTitle(failed)).toBe("Tried moving furniture");
    expect(passTitle({ ...failed, action: null })).toMatch(/^Stopped/);
  });

  it("says which router decided and how much moved", () => {
    const result: LoopResult = { base_revision: 0, decided_by: "local_policy", passes: [base], moves: [] };
    expect(deciderSentence(result.decided_by)).toBe("Our built-in policy chose each step.");
    expect(deciderSentence("typesafe")).toBe("TypeSafe chose each step.");
    expect(summary(result)).toMatch(/^The search found no layout/);
    expect(summary({ ...result, passes: [{ ...base, action: "DONE", problems: 0 }] })).toMatch(/^Nothing needs moving/);
  });

  it("narrates a loop that is still running or was stopped", () => {
    expect(workingSentence(null)).toBe("Getting the shop's measurements ready.");
    expect(workingSentence("typesafe")).toMatch(/^TypeSafe is choosing/);
    expect(stoppedSentence(0)).toMatch(/^Stopped before the first pass/);
    expect(stoppedSentence(2)).toBe("Stopped after 2 passes. Nothing was changed.");
    const running: LoopProgress = { phase: "running", decidedBy: "typesafe", passes: [base], result: null, error: null };
    expect(announcement(running)).toBe("Pass 1: Moved furniture");
    expect(announcement({ ...running, phase: "idle", passes: [] })).toBe("");
  });
});
