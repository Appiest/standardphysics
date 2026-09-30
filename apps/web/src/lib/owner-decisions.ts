import type { Checklist, Finding } from "@/types/contracts";
import { type ChecklistStatus, MOVABLE_CHECKS } from "./owner-journey";

export type StatusOf = (findingId: string) => ChecklistStatus;

/** A problem no furniture move reaches, like a door frame: fixing it is building work. */
export function needsBuilding(finding: Finding): boolean {
  return !MOVABLE_CHECKS.has(finding.check_id);
}

/** Every item's status: what the owner just set, else what the server has, else still to do. */
export function statusLookup(checklist: Checklist, overrides: Record<string, ChecklistStatus>): StatusOf {
  const saved = new Map(checklist.items.map((item) => [item.finding_id, item.status]));
  return (findingId) => overrides[findingId] ?? saved.get(findingId) ?? "to_do";
}

/** The problems still waiting on a decision: any the owner handed to a contractor, ignored or fixed are settled. */
export function openProblems(findings: Finding[], statusOf: StatusOf): Finding[] {
  return findings.filter((finding) => finding.outcome === "problem" && statusOf(finding.id) === "to_do");
}

/** The moment to celebrate: the open count just reached zero from something, and the layout is not mid-check. */
export function justCleared(previous: number, open: number, checking: boolean): boolean {
  return !checking && previous > 0 && open === 0;
}
