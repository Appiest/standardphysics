import type { Finding } from "@/types/contracts";

/**
 * How one finding moved between the scanned layout and the one being tried.
 * `cleared` was a problem and no longer is; `new` is a problem the scan didn't
 * have; `changed` kept its verdict but its measurement moved.
 */
export type ChangeKind = "new" | "cleared" | "changed";

export type FindingChange = { kind: ChangeKind; id: string; before: Finding | null; now: Finding | null };

/** Half an inch: less than that is the tape measure's wobble, not a change anyone did. */
const SMALLEST_CHANGE_INCHES = 0.5;

const KIND_ORDER: Record<ChangeKind, number> = { new: 0, cleared: 1, changed: 2 };

function isProblem(finding: Finding | null | undefined): boolean {
  return finding?.outcome === "problem";
}

function measurementMoved(before: Finding, now: Finding): boolean {
  if (before.measured_inches === null || now.measured_inches === null) return false;
  return Math.abs(before.measured_inches - now.measured_inches) >= SMALLEST_CHANGE_INCHES;
}

function changeKind(before: Finding | null, now: Finding | null): ChangeKind | null {
  if (isProblem(before) && !isProblem(now)) return "cleared";
  if (isProblem(now) && !isProblem(before)) return "new";
  if (before && now && measurementMoved(before, now)) return "changed";
  return null;
}

/** Every finding the tried layout changed, new problems first, then the cleared ones, then numbers that moved. */
export function layoutChanges(before: Finding[], now: Finding[]): FindingChange[] {
  const was = new Map(before.map((finding) => [finding.id, finding]));
  const is = new Map(now.map((finding) => [finding.id, finding]));
  const ids = [...new Set([...was.keys(), ...is.keys()])];
  return ids
    .flatMap((id) => {
      const [previous, current] = [was.get(id) ?? null, is.get(id) ?? null];
      const kind = changeKind(previous, current);
      return kind ? [{ kind, id, before: previous, now: current }] : [];
    })
    .sort((a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind]);
}

/** The problems a layout still has that it didn't change, for the list under the changes. */
export function unchangedProblems(now: Finding[], changes: FindingChange[]): Finding[] {
  const changed = new Set(changes.map((change) => change.id));
  return now.filter((finding) => finding.outcome === "problem" && !changed.has(finding.id));
}

/** The words a change leads with, from the finding that describes the layout it is about. */
export function changeTitle(change: FindingChange): string {
  const finding = change.kind === "cleared" ? change.before : change.now;
  return finding?.title ?? "";
}
