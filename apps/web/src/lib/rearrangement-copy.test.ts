import { describe, expect, it } from "vitest";
import { ApiRefusal } from "@/lib/layout-client";
import { isWorking, phaseSentence, refusalSentence } from "@/lib/rearrangement-copy";
import type { RearrangementStatus } from "@/types/contracts";

function status(state: RearrangementStatus["state"]): RearrangementStatus {
  return { base_revision: 0, available: true, unavailable_reason: null, state, phase: null,
    phase_reason: null, error: null, result: null };
}

describe("rearrangement copy", () => {
  it("says a cold start can take minutes, apart from the ordinary wait", () => {
    expect(phaseSentence("starting_model")).toContain("few minutes");
    expect(phaseSentence("asking_model")).toBe("Asking the model for a layout");
    expect(phaseSentence(null)).toBe("Asking the model for a layout");
    expect(phaseSentence("trying_again", "collided")).toContain("bumped one piece into another");
  });

  it("polls only while a job is queued or running", () => {
    expect(isWorking(status("queued"))).toBe(true);
    expect(isWorking(status("running"))).toBe(true);
    expect(isWorking(status("done"))).toBe(false);
    expect(isWorking(status("idle"))).toBe(false);
    expect(isWorking(null)).toBe(false);
  });

  it("turns refusals into a sentence with a way forward", () => {
    expect(refusalSentence(new ApiRefusal(409, "a newer layout was saved since this one started"))).toContain("Reload");
    expect(refusalSentence(new ApiRefusal(503, "Suggestions need the rearrangement model."))).toBe(
      "Suggestions need the rearrangement model.",
    );
    expect(refusalSentence(new TypeError("fetch failed"))).toContain("Try again");
  });
});
