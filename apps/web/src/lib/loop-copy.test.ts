import { describe, expect, it } from "vitest";
import type { LoopPass, LoopResult } from "@/types/contracts";
import { deciderSentence, passOutcome, passTitle, summary } from "./loop-copy";

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

  it("falls back to the loop's own sentence when no layout was tried", () => {
    const failed = { ...base, kept: null, inches_short_before: null, inches_short_after: null, message: "We couldn't find an arrangement that works." };
    expect(passOutcome(failed)).toBe("We couldn't find an arrangement that works.");
    expect(passTitle(failed)).toBe("Tried moving furniture");
    expect(passTitle({ ...failed, action: null })).toMatch(/^Stopped/);
  });

  it("says which router decided and how much moved", () => {
    const result: LoopResult = { base_revision: 0, decided_by: "local_policy", passes: [base], moves: [] };
    expect(deciderSentence(result)).toBe("Our built-in policy chose each step.");
    expect(deciderSentence({ ...result, decided_by: "typesafe" })).toBe("TypeSafe chose each step.");
    expect(summary(result)).toMatch(/^Nothing/);
  });
});
