import type { Assessment, Scan } from "@/types/contracts";
import { countNeedingAttention } from "./findings";
import { scopedSummary } from "./outcomes";

const STATE_LABEL: Record<Scan["state"], string> = {
  uploading: "Uploading",
  measuring: "Measuring your shop",
  checking: "Checking",
  ready: "Ready",
  failed: "This scan didn't go through. Scan the shop again.",
};

const RULES_WAITING = "Checks start once a person reviews the rules";

/** What ran: how many rules a person has verified, and whether the paths were part of it. */
export type CheckScope = { rulesChecked: number | null; routeConfirmed: boolean };

/** An empty list of problems, said only as widely as the checks that actually ran. */
export function allClearSentence(scope: CheckScope, passes: string): string {
  if (scope.rulesChecked === 0) return RULES_WAITING;
  if (!scope.routeConfirmed) return `${passes} so far. Mark the customer route to check the paths too.`;
  return passes;
}

function scopedStatus(assessment: Assessment, routeConfirmed: boolean, attention: number): string | null {
  const scoped = scopedSummary(assessment.scope ?? null);
  if (scoped === null) return null;
  const pieces = [scoped];
  if (!routeConfirmed && (assessment.rules_checked ?? 0) > 0) {
    pieces.push("Mark the customer route to check the paths too.");
  }
  if (attention > 0) pieces.push(attention === 1 ? "1 finding to look at" : `${attention} findings to look at`);
  return pieces.join(" ");
}

function attentionSentence(attention: number): string | null {
  if (attention === 1) return "1 thing to look at";
  if (attention > 1) return `${attention} things to look at`;
  return null;
}

export function scanStatus(scan: Scan, assessment: Assessment | null, routeConfirmed: boolean): string {
  if (scan.state !== "ready" || assessment === null) return STATE_LABEL[scan.state];
  const attention = countNeedingAttention(assessment.findings);
  return (
    scopedStatus(assessment, routeConfirmed, attention)
    ?? attentionSentence(attention)
    ?? allClearSentence({ rulesChecked: assessment.rules_checked, routeConfirmed }, "Everything we checked passes")
  );
}
