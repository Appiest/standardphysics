import type { SceneGraph, SimulationRequest, SimulationStatus } from "@/types/contracts";

export class SimulationRequestError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({ error: "", need: [] }));
    const fields = Array.isArray(body.need) && body.need.length > 0 ? ` (${body.need.join(", ")})` : "";
    throw new SimulationRequestError(response.status, `${String(body.error || "The request could not be completed.")}${fields}`);
  }
  return (await response.json()) as T;
}

export function rebuildRoom(scanId: string, baseRevision: number) {
  return request<SceneGraph>(`/api/scans/${scanId}/rebuild`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ base_revision: baseRevision }),
  });
}

export function startSimulation(scanId: string, body: SimulationRequest) {
  return request<SimulationStatus>(`/api/scans/${scanId}/simulations`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
}

export function getSimulation(scanId: string, revision: number) {
  return request<SimulationStatus>(`/api/scans/${scanId}/simulations?revision=${revision}`, {
    cache: "no-store",
  });
}
