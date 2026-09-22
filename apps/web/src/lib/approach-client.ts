/** Client for the conservative approach endpoint K froze around R's evaluator. */

import type { ApproachReport, ApproachRequest } from "@/types/contracts";

export async function evaluateApproach(
  scanId: string,
  revision: number,
  body: ApproachRequest,
): Promise<ApproachReport> {
  const response = await fetch(`/api/scans/${scanId}/revisions/${revision}/approach`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!response.ok) {
    throw await response.json().catch(() => ({ error: "Not available right now" }));
  }
  return (await response.json()) as ApproachReport;
}
