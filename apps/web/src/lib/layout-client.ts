import type { AskAnswer, LayoutCheckResult, LoopResult, NodeMove, ProposalResult, Scenario, SceneGraph } from "@/types/contracts";

export class ApiRefusal extends Error {
  constructor(readonly status: number, readonly error: string) {
    super(error);
  }
}

async function sendJson<T>(url: string, body: unknown, method = "POST"): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ error: "" }));
    throw new ApiRefusal(response.status, String(detail.error ?? ""));
  }
  return (await response.json()) as T;
}

export function checkLayout(scanId: string, baseRevision: number, sequence: number, moves: NodeMove[]) {
  return sendJson<LayoutCheckResult>(`/api/scans/${scanId}/layout-checks`, {
    base_revision: baseRevision,
    sequence,
    moves,
  });
}

export function saveLayout(scanId: string, baseRevision: number, moves: NodeMove[]) {
  return sendJson<SceneGraph>(`/api/scans/${scanId}/revisions`, { base_revision: baseRevision, moves });
}

export function proposeFix(scanId: string, baseRevision: number, findingIds: string[]) {
  return sendJson<ProposalResult>(`/api/scans/${scanId}/proposals`, { base_revision: baseRevision, finding_ids: findingIds });
}

export function runLoop(scanId: string, baseRevision: number) {
  return sendJson<LoopResult>(`/api/scans/${scanId}/loop`, { base_revision: baseRevision });
}

export function setCounter(scanId: string, baseRevision: number, nodeId: string, isCounter: boolean) {
  return sendJson<SceneGraph>(`/api/scans/${scanId}/revisions/${baseRevision}/counters/${nodeId}`, undefined, isCounter ? "PUT" : "DELETE");
}

export function confirmRoute(scanId: string, scenario: Scenario) {
  return sendJson<Scenario>(`/api/scans/${scanId}/scenario`, scenario, "PUT");
}

export function askAboutShop(scanId: string, baseRevision: number, text: string) {
  return sendJson<AskAnswer>(`/api/scans/${scanId}/ask`, { base_revision: baseRevision, text });
}
