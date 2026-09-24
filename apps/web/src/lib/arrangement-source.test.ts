import { describe, expect, it } from "vitest";
import { type ArrangementEvent, type MovesSource, pendingLabels, sourceAfter } from "@/lib/arrangement-source";

function afterLabelFollowing(events: ArrangementEvent[]): string {
  const source = events.reduce<MovesSource>((_, event) => sourceAfter(event), null);
  return pendingLabels(source).afterLabel;
}

describe("before and after labels for pending moves", () => {
  it("calls a loaded suggestion Suggested", () => {
    expect(pendingLabels(sourceAfter("suggested"))).toEqual({ beforeLabel: "Now", afterLabel: "Suggested" });
  });

  it("calls it the owner's once they drag something on top of it", () => {
    expect(afterLabelFollowing(["suggested", "moved"])).toBe("With your moves");
  });

  it("calls the owner's own moves and a fix they tried their moves", () => {
    expect(afterLabelFollowing(["moved"])).toBe("With your moves");
    expect(afterLabelFollowing(["loaded"])).toBe("With your moves");
    expect(afterLabelFollowing(["suggested", "cleared", "moved"])).toBe("With your moves");
  });
});
