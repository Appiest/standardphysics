import { describe, expect, it } from "vitest";
import type { Finding, LayoutPlan } from "@/types/contracts";
import { decisions, planOutcome } from "./reportPlan";

const citation = { authority: "ADA_2010", edition: "2010 ADA Standards", section: "403.5.1", url: null } as const;

function finding(id: string, outcome: Finding["outcome"]): Finding {
  return { id, check_id: "route_clear_width", outcome, title: id, detail: "", fix: null, measured_inches: 30, required_inches: 36, citation, asks: null, locus: null };
}

const move = { node_id: "table", delta_translation: { x: 0.5, y: 0, z: 0 }, delta_rotation_z_degrees: 0 };

describe("report plan", () => {
  it("lists the problems the kept layout clears, passing or gone, and counts the pieces it moves", () => {
    const plan: LayoutPlan = { id: "p", scan_id: "s", base_revision: 0, name: "Layout 1", created_at: "2026-09-30T00:00:00Z", moves: [move],
      findings: [finding("aisle", "passes"), finding("door", "problem")] };
    const outcome = planOutcome([finding("aisle", "problem"), finding("door", "problem"), finding("seat", "problem")], plan);
    expect(outcome.cleared.map((cleared) => cleared.id)).toEqual(["aisle", "seat"]);
    expect(outcome.moved).toBe(1);
  });

  it("keeps only the problems the owner decided about", () => {
    const marked = decisions({ items: [{ finding_id: "door", status: "needs_pro", updated_at: null }, { finding_id: "aisle", status: "to_do", updated_at: null }], done: 1, total: 2 });
    expect([...marked.entries()]).toEqual([["door", "needs_pro"]]);
    expect(decisions(null).size).toBe(0);
  });
});
