import { describe, expect, it } from "vitest";
import type { Finding } from "@/types/contracts";
import { changeTitle, layoutChanges, unchangedProblems } from "./layout-changes";

const citation = { authority: "ADA_2010", edition: "2010 ADA Standards", section: "403.5.1", url: null } as const;

function finding(id: string, outcome: Finding["outcome"], measured: number | null = null, title = id): Finding {
  return {
    id, check_id: "route_clear_width", outcome, title, detail: "", fix: null,
    measured_inches: measured, required_inches: 36, citation, asks: null, locus: null,
  };
}

describe("layoutChanges", () => {
  it("names a problem the move made, one it cleared, and a number it moved, new ones first", () => {
    const before = [finding("aisle", "problem", 31), finding("exit", "passes", 74.9), finding("seat", "passes", 50)];
    const now = [finding("aisle", "passes", 38), finding("exit", "problem", 28.7), finding("seat", "passes", 44)];
    expect(layoutChanges(before, now).map((change) => [change.kind, change.id])).toEqual([
      ["new", "exit"], ["cleared", "aisle"], ["changed", "seat"],
    ]);
  });

  it("ignores a wobble under half an inch and a finding that kept its verdict and number", () => {
    const before = [finding("aisle", "problem", 31), finding("seat", "question", 50)];
    const now = [finding("aisle", "problem", 31.3), finding("seat", "question", 50)];
    expect(layoutChanges(before, now)).toEqual([]);
  });

  it("counts a problem that disappears from the list as cleared, and one that appears as new", () => {
    const changes = layoutChanges([finding("gone", "problem")], [finding("fresh", "problem")]);
    expect(changes.map((change) => [change.kind, change.id])).toEqual([["new", "fresh"], ["cleared", "gone"]]);
  });

  it("titles a cleared change by the problem it was, and a new one by the problem it is", () => {
    const [made, cleared] = layoutChanges(
      [finding("a", "problem", 31, "The aisle is too narrow"), finding("b", "passes", 60, "The exit fits")],
      [finding("a", "passes", 40, "The aisle fits"), finding("b", "problem", 20, "The exit is too narrow")],
    );
    expect([changeTitle(made), changeTitle(cleared)]).toEqual(["The exit is too narrow", "The aisle is too narrow"]);
  });
});

describe("unchangedProblems", () => {
  it("keeps only the problems no change already mentions", () => {
    const now = [finding("a", "problem", 20), finding("b", "problem", 31), finding("c", "passes")];
    const changes = layoutChanges([finding("a", "passes", 40), finding("b", "problem", 31)], now);
    expect(unchangedProblems(now, changes).map((f) => f.id)).toEqual(["b"]);
  });
});
