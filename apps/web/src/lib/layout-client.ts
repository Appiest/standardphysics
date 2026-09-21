import { readLines } from "@/lib/ndjson";
import type { AskAnswer, LayoutCheckResult, LoopEvent, NodeMove, ProposalResult, Scenario, SceneGraph } from "@/types/contracts";

export class ApiRefusal extends Error {
  constructor(readonly status: number, readonly error: string) {
    super(error);
  }
}

async function refusal(response: Response) {
  const detail = await response.json().catch(() => ({ error: "", need: [] }));
  const fields = Array.isArray(detail.need) && detail.need.length > 0 ? ` (${detail.need.join(", ")})` : "";
  return new ApiRefusal(response.status, `${String(detail.error ?? "")}${fields}`);
}

async function sendJson<T>(url: string, body: unknown, method = "POST"): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await refusal(response);
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

export type CombineRoom = {
  node_ids: string[];
  yaw_degrees: number;
  tx: number;
  ty: number;
  cx: number;
  cy: number;
};

export function saveCombine(scanId: string, baseRevision: number, rooms: CombineRoom[]) {
  return sendJson<SceneGraph>(`/api/scans/${scanId}/combine`, { base_revision: baseRevision, rooms });
}

export function proposeFix(scanId: string, baseRevision: number, findingIds: string[]) {
  return sendJson<ProposalResult>(`/api/scans/${scanId}/proposals`, { base_revision: baseRevision, finding_ids: findingIds });
}

/** Runs the loop and hands over each event as the server sends it; resolves when the stream closes. */
export async function streamLoop(scanId: string, baseRevision: number, onEvent: (event: LoopEvent) => void, signal: AbortSignal) {
  const response = await fetch(`/api/scans/${scanId}/loop/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ base_revision: baseRevision }),
    signal,
  });
  if (!response.ok || !response.body) throw await refusal(response);
  for await (const line of readLines(response.body)) onEvent(JSON.parse(line) as LoopEvent);
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
