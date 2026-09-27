import type { Finding, ReviewedRule } from "@/types/contracts";

export type OpenQuestions = { toSend: Finding[]; beingChecked: Finding[] };

/**
 * A question the owner has already answered, waiting for a person on the team.
 * The server marks it `asks: "review"` (services/api/standardphysics_api/answered_findings.py).
 */
export function isBeingChecked(finding: Finding): boolean {
  return finding.outcome === "question" && finding.asks === "review";
}

/** Questions the owner still has to answer, apart from photos they already sent. */
export function splitQuestions(questions: Finding[]): OpenQuestions {
  return {
    toSend: questions.filter((finding) => !isBeingChecked(finding)),
    beingChecked: questions.filter(isBeingChecked),
  };
}

/** What each photo being checked shows, by the name its rule gives it ("The front door handle"). */
export function beingCheckedNames(beingChecked: Finding[], rules: ReviewedRule[]): string[] {
  const ruleNames = new Map(rules.map((rule) => [rule.check.id, rule.check.title]));
  return beingChecked.map((finding) => ruleNames.get(finding.check_id) ?? finding.citation.section);
}
