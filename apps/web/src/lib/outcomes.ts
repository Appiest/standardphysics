import type { ScopeManifest, ScopeRow } from "@/types/contracts";

export type OutcomeCounts = Record<ScopeRow["outcome"], number>;

export const OUTCOME_LABEL: Record<ScopeRow["outcome"], string> = {
  satisfied: "Satisfied",
  violation: "Violation",
  needs_verification: "Needs verification",
  not_applicable: "Not applicable",
  unobserved: "Unobserved",
};

export const LEGAL_REVIEW_LABEL: Record<ScopeRow["legal_review_status"], string> = {
  unreviewed_preview: "Automated preview, not reviewed by a person",
  needs_review: "Waiting for a person to review the rule",
  reviewer_supplied: "Reviewed by a person",
};

export function countOutcomes(rows: ScopeRow[]): OutcomeCounts {
  const counts: OutcomeCounts = {
    satisfied: 0,
    violation: 0,
    needs_verification: 0,
    not_applicable: 0,
    unobserved: 0,
  };
  for (const row of rows) counts[row.outcome] += 1;
  return counts;
}

/** Rows grouped by the thing they're about, keeping the manifest's order. */
export function rowsByItem(rows: ScopeRow[]): { item_key: string; label: string; rows: ScopeRow[] }[] {
  const groups: { item_key: string; label: string; rows: ScopeRow[] }[] = [];
  const byKey = new Map<string, number>();
  for (const row of rows) {
    const key = row.item.item_slug;
    const existing = byKey.get(key);
    if (existing === undefined) {
      byKey.set(key, groups.length);
      groups.push({ item_key: key, label: row.item.label, rows: [row] });
    } else {
      groups[existing].rows.push(row);
    }
  }
  return groups;
}

/**
 * One honest sentence about the scoped checks. Never a whole-site compliance
 * claim: green outcomes only describe the checks that ran on captured evidence,
 * and the person-review state rides on top.
 */
export function scopedSummary(scope: ScopeManifest | null): string | null {
  if (scope === null || scope.rows.length === 0) return null;
  const counts = countOutcomes(scope.rows);
  const parts = [
    `${counts.satisfied} satisfied`,
    `${counts.violation} violation${counts.violation === 1 ? "" : "s"}`,
    `${counts.needs_verification} need verification`,
    `${counts.unobserved} unobserved`,
  ];
  if (counts.not_applicable > 0) parts.push(`${counts.not_applicable} not applicable`);
  return `Scoped checks: ${parts.join(", ")}.`;
}

/** Whether every row landed in a definite, evidence-backed place with no person still needed. */
export function scopeFullyResolved(scope: ScopeManifest): boolean {
  return (
    scope.rows.every((row) => row.outcome === "satisfied" || row.outcome === "not_applicable") &&
    scope.unresolved_questions.length === 0 &&
    scope.applicability_questions.length === 0
  );
}

/** A row's pending facts are the ones a person can act on: applicability and legal review. */
export function pendingFacts(scope: ScopeManifest): string[] {
  const facts: string[] = [];
  for (const question of scope.applicability_questions) facts.push(`Applicability question: ${question}`);
  for (const question of scope.unresolved_questions) facts.push(`Unresolved: ${question}`);
  for (const area of scope.unobserved_areas) facts.push(`Unobserved area: ${area}`);
  return facts;
}
