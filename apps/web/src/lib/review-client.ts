import type { EvidenceStatus, ManualMarkRequest, SceneGraph } from "@/types/contracts";

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

/** The four processing lanes of a scan, exactly as the server sees them. */
export async function getEvidence(scanId: string): Promise<EvidenceStatus | null> {
  const response = await fetch(`/api/scans/${scanId}/evidence`, { cache: "no-store" });
  if (response.status === 404) return null;
  if (!response.ok) throw await refusal(response);
  return (await response.json()) as EvidenceStatus;
}

/** A person's photo mark: evidence where the pipeline found nothing. */
export function markObservation(
  scanId: string,
  baseRevision: number,
  mark: Omit<ManualMarkRequest, "note"> & { note?: string | null }
): Promise<SceneGraph> {
  return sendJson<SceneGraph>(
    `/api/scans/${scanId}/revisions/${baseRevision}/observations`,
    { note: null, ...mark },
    "PUT"
  );
}

/** The authenticated crop image for an observation, cut from the actual frame bytes. */
export function cropUrl(scanId: string, cropId: string): string {
  return `/api/scans/${scanId}/crops/${encodeURIComponent(cropId)}`;
}

/** An original frame photo, for a person to look at and mark. 404 until the route ships. */
export function frameUrl(scanId: string, frameId: string): string {
  return `/api/scans/${scanId}/frames/${encodeURIComponent(frameId)}`;
}

/** Confirm or reject a photographed object, so the person's decision rides with the evidence. */
export function reviewAttachment(
  scanId: string,
  baseRevision: number,
  nodeId: string,
  status: "confirmed_by_user" | "rejected_by_user" | "candidate" | "detected"
): Promise<SceneGraph> {
  return sendJson<SceneGraph>(
    `/api/scans/${scanId}/revisions/${baseRevision}/outlets/${nodeId}/review`,
    { status },
    "PUT"
  );
}

/** Whether this sensor box is a rectangle a crop could be cut from. */
export function isPlausibleSensorBox(box: number[]): boolean {
  return box.length === 4 && box.every((value) => Number.isFinite(value)) && box[2] > box[0] && box[3] > box[1];
}
