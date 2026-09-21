import { describe, expect, it } from "vitest";
import { ADA_RULES, boundsEstimate, inspectionCategory, inspectionLimitations } from "./wheelchair-inspection";
import type { SceneNode } from "@/types/contracts";

const node = (overrides: Partial<SceneNode> = {}): SceneNode => ({
  id: "object-1",
  kind: "object",
  label: "Table",
  raw_category: "table",
  dimensions: { x: 1.2, y: 0.7, z: 0.75 },
  transform: { m: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] },
  labeled_by: "roomplan",
  movable: true,
  parent_id: null,
  quality: "measured",
  ...overrides,
});

describe("wheelchair inspection", () => {
  it("reports scan object bounds without treating its height as a tabletop", () => {
    expect(boundsEstimate(node()).heightInches).toBeCloseTo(29.53, 2);
    expect(inspectionLimitations(inspectionCategory(node()))).toContain(
      "No measured tabletop height above finished floor.",
    );
  });

  it("keeps service counters distinct from a dining/work-surface candidate", () => {
    expect(inspectionCategory(node({ label: "Ordering counter", raw_category: "counter" }))).toBe("service_or_sales_counter");
    expect(inspectionCategory(node({ label: "Counter", raw_category: "counter" }))).toBe("unclassified_counter");
  });

  it("links only conditional rules and never turns a center distance into a reach pass", () => {
    expect(ADA_RULES.map((rule) => rule.section)).toEqual(["902.3", "306", "308"]);
    expect(ADA_RULES[2].caveat).toContain("center distance is context only");
  });
});
