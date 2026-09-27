import type { ReviewedRule } from "@/types/contracts";

export type SharedReview = { reviewedBy: string; reviewedOn: string; secondCheckBy: string | null };

const day = (instant: string) => instant.slice(0, 10);

/** The one review every rule shares, so the report can say it once instead of on every row. */
export function sharedReview(rules: ReviewedRule[]): SharedReview | null {
  const [first] = rules;
  if (!first) return null;
  const same = rules.every(
    (rule) =>
      rule.verified_by === first.verified_by &&
      day(rule.verified_at) === day(first.verified_at) &&
      rule.second_check_by === first.second_check_by,
  );
  return same ? { reviewedBy: first.verified_by, reviewedOn: first.verified_at, secondCheckBy: first.second_check_by } : null;
}

const UNIT_WORDS: Record<string, string> = { lbf: "lb" };

/** A rule's number the way the report shows it: inches as inches, force in pounds. */
export function thresholdText(value: number, unit: string, inches: (value: number) => string): string {
  if (unit === "in") return inches(value);
  return `${value} ${UNIT_WORDS[unit] ?? unit}`;
}
