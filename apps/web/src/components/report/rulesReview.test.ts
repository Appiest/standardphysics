import { describe, expect, it } from "vitest";
import { formatInches } from "@/lib/findings";
import type { ReviewedRule } from "@/types/contracts";
import { sharedReview, thresholdText } from "./rulesReview";

function rule(id: string, verifiedBy: string, verifiedAt: string, secondCheckBy: string | null = null): ReviewedRule {
  return {
    verified_by: verifiedBy, verified_at: verifiedAt, second_check_by: secondCheckBy,
    check: { id, title: id, threshold: 36, unit: "in", citation: { authority: "ADA_2010", edition: "2010 ADA Standards", section: "403.5.1", url: null } } as ReviewedRule["check"],
  };
}

describe("sharedReview", () => {
  it("says the review once when every rule was read by the same person on the same day", () => {
    const rules = [rule("a", "Dana Ruiz", "2026-09-14T02:47:53.098Z"), rule("b", "Dana Ruiz", "2026-09-14T02:47:54.001Z")];
    expect(sharedReview(rules)).toEqual({ reviewedBy: "Dana Ruiz", reviewedOn: "2026-09-14T02:47:53.098Z", secondCheckBy: null });
  });

  it("keeps a review per rule when the reviewers differ", () => {
    expect(sharedReview([rule("a", "Dana Ruiz", "2026-09-14T00:00:00Z"), rule("b", "Sam Lee", "2026-09-14T00:00:00Z")])).toBeNull();
  });

  it("keeps a review per rule when only some had a second check", () => {
    expect(sharedReview([rule("a", "Dana Ruiz", "2026-09-14T00:00:00Z", "Sam Lee"), rule("b", "Dana Ruiz", "2026-09-14T00:00:00Z")])).toBeNull();
  });

  it("has nothing to say about no rules", () => {
    expect(sharedReview([])).toBeNull();
  });
});

describe("thresholdText", () => {
  it("writes inches as inches and push force in pounds", () => {
    expect([thresholdText(36, "in", formatInches), thresholdText(0.5, "in", formatInches), thresholdText(5, "lbf", formatInches)]).toEqual(["36 in", "0.5 in", "5 lb"]);
  });
});
