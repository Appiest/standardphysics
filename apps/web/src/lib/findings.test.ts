import { describe, expect, it } from "vitest";
import type { Finding } from "@/types/contracts";
import { findingForNode, formatInches, groupFindings } from "./findings";

const citation = { authority: "ADA_2010", edition: "2010 ADA Standards", section: "403.5.1", url: null } as const;

function finding(id: string, outcome: Finding["outcome"], nodeIds: string[] = []): Finding {
  const point = { x: 0, y: 0, z: 0 };
  return {
    id, check_id: "route_clear_width", outcome, title: id, detail: "", fix: null,
    measured_inches: null, required_inches: null, citation, asks: null,
    locus: nodeIds.length === 0 ? null : {
      point, bbox_min: point, bbox_max: point, node_ids: nodeIds, render_url: null,
      annotation: { kind: "dimension_line", points: [point, point], label: "31 in", point_inches: null },
      camera: { position: point, target: point, fov_degrees: 50 },
    },
  };
}

describe("groupFindings", () => {
  it("keeps problems, questions and passes apart in their original order", () => {
    const groups = groupFindings([finding("a", "passes"), finding("b", "problem"), finding("c", "question"), finding("d", "problem")]);
    expect(groups.problems.map((f) => f.id)).toEqual(["b", "d"]);
    expect(groups.questions.map((f) => f.id)).toEqual(["c"]);
    expect(groups.passes.map((f) => f.id)).toEqual(["a"]);
  });
});

describe("findingForNode", () => {
  it("prefers a problem over a pass on the same object", () => {
    const found = findingForNode([finding("ok", "passes", ["case"]), finding("tight", "problem", ["case"])], "case");
    expect(found?.id).toBe("tight");
  });
});

describe("formatInches", () => {
  it("drops the decimal on whole inches and keeps one place otherwise", () => {
    expect([formatInches(30.99999), formatInches(29.79), formatInches(35.98)]).toEqual(["31 in", "29.8 in", "36 in"]);
  });
});
