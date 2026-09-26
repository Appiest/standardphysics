import { ApiRefusal } from "@/lib/layout-client";
import type { ChecklistStatus } from "@/lib/owner-journey";
import type {
  ChecklistItem, LayoutPlan, NodeMove, OwnerRequest, RouteLegs, Scenario, SceneGraph, Session, ShareLink,
} from "@/types/contracts";

async function send<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ error: "" }));
    throw new ApiRefusal(response.status, String(detail.error ?? ""));
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const answerRequest = (scanId: string, requestId: string, answer: { yes: boolean } | { number: number }) =>
  send<OwnerRequest>(`/api/scans/${scanId}/requests/${requestId}/answer`, json("PUT", answer));

export const skipRequest = (scanId: string, requestId: string) =>
  send<OwnerRequest>(`/api/scans/${scanId}/requests/${requestId}/skip`, json("POST"));

export const sendPhoto = (scanId: string, requestId: string, photo: Blob) =>
  send<OwnerRequest>(`/api/scans/${scanId}/requests/${requestId}/photo`, {
    method: "PUT",
    headers: { "Content-Type": photo.type || "image/jpeg" },
    body: photo,
  });

export const markStatus = (scanId: string, findingId: string, status: ChecklistStatus) =>
  send<ChecklistItem>(`/api/scans/${scanId}/checklist/${findingId}`, json("PUT", { status }));

export const suggestPath = (scanId: string, destinations: string[]) =>
  send<Scenario>(`/api/scans/${scanId}/scenario/suggestion?destinations=${encodeURIComponent(destinations.join(","))}`, { method: "GET" });

export const walkingRoute = (scanId: string, scenario: Scenario) =>
  send<RouteLegs>(`/api/scans/${scanId}/scenario/legs`, json("POST", scenario));

export const markCounter = (scanId: string, baseRevision: number, nodeId: string) =>
  send<SceneGraph>(`/api/scans/${scanId}/revisions/${baseRevision}/counters/${nodeId}`, json("PUT"));

export const shareReport = (scanId: string) => send<ShareLink>(`/api/scans/${scanId}/shares`, json("POST"));

export const stopSharing = (scanId: string) => send<void>(`/api/scans/${scanId}/shares`, { method: "DELETE" });

export const savePlan = (scanId: string, baseRevision: number, moves: NodeMove[]) =>
  send<LayoutPlan>(`/api/scans/${scanId}/plans`, json("POST", { base_revision: baseRevision, moves }));

export const saveAccount = (email: string, password: string) =>
  send<Session>("/api/auth/save", json("POST", { email, password }));
