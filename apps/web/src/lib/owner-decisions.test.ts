import { describe, expect, it } from "vitest";
import type { Checklist, Finding } from "@/types/contracts";
import { justCleared, needsBuilding, openProblems, statusLookup } from "./owner-decisions";

const citation = { authority: "ADA_2010", edition: "2010 ADA Standards", section: "404.2.3", url: null } as const;

function finding(id: string, checkId: string, outcome: Finding["outcome"] = "problem"): Finding {
  return { id, check_id: checkId, outcome, title: id, detail: "", fix: null, measured_inches: 29.7, required_inches: 32, citation, asks: null, locus: null };
}

const checklist: Checklist = { items: [{ finding_id: "door", status: "needs_pro", updated_at: null }], done: 1, total: 1 };

describe("owner decisions", () => {
  it("tells building work from what a furniture move can reach", () => {
    expect(needsBuilding(finding("door", "door_clear_width"))).toBe(true);
    expect(needsBuilding(finding("aisle", "route_clear_width"))).toBe(false);
  });

  it("reads a status the owner just set over the saved one, and to do for anything unmarked", () => {
    const statusOf = statusLookup(checklist, { aisle: "not_doing" });
    expect([statusOf("door"), statusOf("aisle"), statusOf("seat")]).toEqual(["needs_pro", "not_doing", "to_do"]);
    expect(statusLookup(checklist, { door: "to_do" })("door")).toBe("to_do");
  });

  it("leaves out settled problems and anything that is not a problem", () => {
    const findings = [finding("door", "door_clear_width"), finding("aisle", "route_clear_width"), finding("seat", "route_clear_width", "passes")];
    expect(openProblems(findings, statusLookup(checklist, {})).map((open) => open.id)).toEqual(["aisle"]);
  });

  it("celebrates only the step down to zero, and never while a check is out", () => {
    expect(justCleared(2, 0, false)).toBe(true);
    expect(justCleared(0, 0, false)).toBe(false);
    expect(justCleared(2, 0, true)).toBe(false);
    expect(justCleared(2, 1, false)).toBe(false);
  });
});
