import { describe, expect, it } from "vitest";
import type { Assessment, Scan } from "@/types/contracts";
import { allClearSentence, scanStatus } from "./scan-status";

const scan = { state: "ready" } as Scan;
const assessment = (rulesChecked: number | null): Assessment => ({ findings: [], rules_checked: rulesChecked }) as unknown as Assessment;

describe("scanStatus", () => {
  it("never calls an unchecked shop a pass", () => {
    expect(scanStatus(scan, assessment(0), true)).toBe("Checks start once a person reviews the rules");
    expect(scanStatus(scan, assessment(0), false)).toBe("Checks start once a person reviews the rules");
  });

  it("calls a checked shop with no findings a pass", () => {
    expect(scanStatus(scan, assessment(13), true)).toBe("Everything we checked passes");
  });

  it("asks for the route before calling a shop without one a pass", () => {
    expect(scanStatus(scan, assessment(8), false)).toBe(
      "Everything we checked passes so far. Mark the customer route to check the paths too.",
    );
  });

  it("shows the processing state until there is an assessment", () => {
    expect(scanStatus({ state: "failed" } as Scan, null, false)).toBe("This scan didn't go through. Scan the shop again.");
    expect(scanStatus(scan, null, false)).toBe("Ready");
  });

  it("a scoped outcome matrix replaces the one-line pass with the counted outcomes, never a green claim", () => {
    const withScope = (rows: number, outcome: string): Assessment => {
      const row = { outcome, requirement_id: "reach", requested: true, applicability: "unknown", applicability_facts: [], reason: null, evidence_refs: [], measurement: null, source_version: "rules-v1", legal_review_status: "unreviewed_preview", item: { item_id: null, item_slug: "outlet", item_kind: "class", label: "Outlets", observed: true, source: "requested_not_observed" } };
      return {
        findings: [],
        rules_checked: 3,
        scope: {
          id: "m-1",
          scan_id: "s-1",
          version: 1,
          created_at: "2026-09-21T00:00:00Z",
          graph_revision: 1,
          graph_hash: "h",
          rulepack_version: "rules-v1",
          manifest_hash: "mh",
          surveyed_areas: [],
          unobserved_areas: [],
          route_endpoints: [],
          requested_classes: [],
          requested_requirements: [],
          applicability_questions: [],
          unresolved_questions: [],
          rows: Array.from({ length: rows }, () => row),
        },
      } as unknown as Assessment;
    };
    expect(scanStatus(scan, withScope(1, "unobserved"), true)).toContain("1 unobserved");
    expect(scanStatus(scan, withScope(2, "satisfied"), true)).not.toContain("Everything we checked passes");
    expect(scanStatus(scan, withScope(1, "violation"), true)).toContain("1 violation");
  });
});

describe("allClearSentence", () => {
  it("keeps a layout's pass inside what ran", () => {
    const passes = "This layout passes everything we checked";
    expect(allClearSentence({ rulesChecked: 13, routeConfirmed: true }, passes)).toBe(passes);
    expect(allClearSentence({ rulesChecked: null, routeConfirmed: false }, passes)).toBe(`${passes} so far. Mark the customer route to check the paths too.`);
    expect(allClearSentence({ rulesChecked: 0, routeConfirmed: true }, passes)).toBe("Checks start once a person reviews the rules");
  });
});
