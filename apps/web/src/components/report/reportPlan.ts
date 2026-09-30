import type { Checklist, Finding, LayoutPlan } from "@/types/contracts";

export type Decision = "done" | "not_doing" | "needs_pro";

/** What the owner decided about each problem they marked; anything unmarked is still to do and absent here. */
export function decisions(checklist: Checklist | null | undefined): Map<string, Decision> {
  const marked = (checklist?.items ?? []).filter((item) => item.status !== "to_do");
  return new Map(marked.map((item) => [item.finding_id, item.status as Decision]));
}

/** The shop's problems the kept layout no longer has, gone or passing, and how many pieces it moves. */
export function planOutcome(problems: Finding[], plan: LayoutPlan) {
  const after = new Map(plan.findings.map((finding) => [finding.id, finding.outcome]));
  const cleared = problems.filter((finding) => after.get(finding.id) !== "problem");
  return { cleared, moved: plan.moves.length };
}
