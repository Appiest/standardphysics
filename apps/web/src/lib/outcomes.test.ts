import { describe, expect, it } from "vitest";
import { countOutcomes, pendingFacts, rowsByItem, scopedSummary, scopeFullyResolved } from "@/lib/outcomes";
import type { ScopeManifest, ScopeRow } from "@/types/contracts";

const row = (overrides: Partial<ScopeRow> = {}): ScopeRow => ({
  item: { item_id: null, item_kind: "class", item_slug: "outlet-class", label: "Outlets", observed: true, source: "requested_not_observed" },
  requirement_id: "reach-height",
  requested: true,
  applicability: "unknown",
  applicability_facts: [],
  applicability_reason: null,
  outcome: "needs_verification",
  reason: null,
  evidence_refs: [],
  measurement: null,
  source_version: null,
  legal_review_status: "unreviewed_preview",
  ...overrides,
});

const manifest = (rows: ScopeRow[], overrides: Partial<ScopeManifest> = {}): ScopeManifest => ({
  id: "manifest-1",
  scan_id: "scan-1",
  version: 2,
  created_at: "2026-09-21T00:00:00Z",
  graph_revision: 4,
  graph_hash: "hash",
  rulepack_version: "rules-v2",
  manifest_hash: "mhash",
  surveyed_areas: [],
  unobserved_areas: [],
  route_endpoints: [],
  requested_classes: [],
  requested_requirements: [],
  applicability_questions: [],
  unresolved_questions: [],
  rows,
  ...overrides,
});

describe("scoped outcome matrix helpers", () => {
  it("counts every outcome, including the ones nobody wants to highlight", () => {
    const counts = countOutcomes([
      row({ outcome: "satisfied" }),
      row({ outcome: "satisfied" }),
      row({ outcome: "violation" }),
      row({ outcome: "unobserved" }),
      row({ outcome: "needs_verification" }),
      row({ outcome: "not_applicable" }),
    ]);
    expect(counts).toEqual({ satisfied: 2, violation: 1, needs_verification: 1, not_applicable: 1, unobserved: 1 });
  });

  it("the summary sentence never reads as a compliance claim", () => {
    const scope = manifest([row({ outcome: "satisfied" }), row({ outcome: "unobserved" })]);
    const summary = scopedSummary(scope);
    expect(summary).toContain("1 satisfied");
    expect(summary).toContain("1 unobserved");
    expect(summary).not.toContain("compliant");
  });

  it("no scope rows means no summary, not a green sentence", () => {
    expect(scopedSummary(manifest([]))).toBeNull();
    expect(scopedSummary(null)).toBeNull();
  });

  it("rows group by the thing they are about", () => {
    const groups = rowsByItem([
      row({ item: { ...row().item, item_slug: "counter-1", label: "Service counter" } }),
      row({ requirement_id: "a" }),
      row({ requirement_id: "b", item: { ...row().item, item_slug: "counter-1", label: "Service counter" } }),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups.find((group) => group.item_key === "counter-1")?.rows).toHaveLength(2);
  });

  it("pending facts collect applicability questions, unresolved questions and unobserved areas", () => {
    const scope = manifest([row()], {
      applicability_questions: ["Does the counter have a customer side?"],
      unresolved_questions: ["Restroom availability not confirmed"],
      unobserved_areas: ["back hall"],
    });
    expect(pendingFacts(scope)).toEqual([
      "Applicability question: Does the counter have a customer side?",
      "Unresolved: Restroom availability not confirmed",
      "Unobserved area: back hall",
    ]);
  });

  it("a fully resolved scope still needs a person for legal review status and cannot read as certified", () => {
    const resolved = manifest([row({ outcome: "satisfied", legal_review_status: "unreviewed_preview" })]);
    expect(scopeFullyResolved(resolved)).toBe(true);
    expect(scopedSummary(resolved)).not.toContain("ADA compliant");
  });
});
