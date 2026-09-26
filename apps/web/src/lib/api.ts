import { cookies } from "next/headers";
import type { Assessment, Checklist, EvidenceStatus, Journey, JourneyList, Report, Scan, ScanList, Scenario, SceneGraph, ShopRequests, SimulationReplay, TextureStatus } from "@/types/contracts";
import type { RoomGroup } from "./room-groups";
import type { CapturedSplats } from "./captured-splats";
import { API_ORIGIN } from "./api-origin";

export class NotReady extends Error {}
export class NotSignedIn extends Error {}

export const SESSION_COOKIE = "sp_session";

/** The browser's session, forwarded by hand.
 *
 * These functions run on the server, where a fetch carries no cookie jar of
 * its own. Without this the API would answer 401 to a signed-in owner. */
async function sessionHeader(): Promise<HeadersInit> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  return token ? { cookie: `${SESSION_COOKIE}=${token}` } : {};
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_ORIGIN}${path}`, { cache: "no-store", headers: await sessionHeader() });
  if (response.status === 401) throw new NotSignedIn(path);
  if (response.status === 404) throw new NotReady(path);
  if (!response.ok) throw new Error(`${path} answered ${response.status}`);
  return (await response.json()) as T;
}

async function getOptional<T>(path: string): Promise<T | null> {
  try {
    return await getJson<T>(path);
  } catch (error) {
    if (error instanceof NotReady) return null;
    throw error;
  }
}

export const listScans = async () => (await getJson<ScanList>("/api/scans")).scans;
export const getScan = (scanId: string) => getOptional<Scan>(`/api/scans/${scanId}`);
export const getScene = (scanId: string, revision?: number) =>
  getOptional<SceneGraph>(`/api/scans/${scanId}/scene${revision === undefined ? "" : `?revision=${revision}`}`);
export const getScenario = (scanId: string) => getOptional<Scenario>(`/api/scans/${scanId}/scenario`);
export const getAssessment = (scanId: string, revision?: number) =>
  getOptional<Assessment>(`/api/scans/${scanId}/assessment${revision === undefined ? "" : `?revision=${revision}`}`);

export const sceneGlbUrl = (scanId: string, revision?: number) => `/api/scans/${scanId}/scene.glb${revision === undefined ? "" : `?revision=${revision}`}`;

/** Whether the exported model is there yet, without pulling it down. */
export const headSceneGlb = async (scanId: string, revision?: number) =>
  fetch(`${API_ORIGIN}${sceneGlbUrl(scanId, revision)}`, {
    method: "HEAD",
    cache: "no-store",
    headers: await sessionHeader(),
  });
export const getReport = (scanId: string) => getOptional<Report>(`/api/scans/${scanId}/report`);
export const getScenarioSuggestion = (scanId: string) => getOptional<Scenario>(`/api/scans/${scanId}/scenario/suggestion`);
export const getSimulationReplay = (scanId: string, revision: number) =>
  getOptional<SimulationReplay>(`/api/scans/${scanId}/revisions/${revision}/replay`);
export const getTextureStatus = (scanId: string, revision: number) =>
  getOptional<TextureStatus>(`/api/scans/${scanId}/textures?revision=${revision}`);

export const getRooms = (scanId: string, revision: number) =>
  getOptional<{ rooms: RoomGroup[] }>(`/api/scans/${scanId}/rooms?revision=${revision}`);

export const getCapturedSplats = (scanId: string, revision: number) =>
  getOptional<CapturedSplats>(`/api/scans/${scanId}/splats?revision=${revision}`);

export const getEvidence = (scanId: string) =>
  getOptional<EvidenceStatus>(`/api/scans/${scanId}/evidence`);

export const getJourney = (scanId: string) => getOptional<Journey>(`/api/scans/${scanId}/journey`);
export const listJourneys = async () => (await getJson<JourneyList>("/api/journeys")).journeys;
export const getRequests = async (scanId: string) => (await getOptional<ShopRequests>(`/api/scans/${scanId}/requests`))?.requests ?? [];
export const getPathSuggestion = (scanId: string, places: string[]) =>
  getOptional<Scenario>(`/api/scans/${scanId}/scenario/suggestion?destinations=${encodeURIComponent(places.join(","))}`);
/** A report behind a share link, or the example shop behind the token "example". No sign-in needed. */
export const getSharedReport = (token: string) => getOptional<Report>(`/api/shared/${encodeURIComponent(token)}`);

export const getChecklist = (scanId: string) => getOptional<Checklist>(`/api/scans/${scanId}/checklist`);

/** The exported model's address, once the server has one to send. */
export async function readyGlbUrl(scanId: string): Promise<string | null> {
  const response = await headSceneGlb(scanId);
  return response.ok ? sceneGlbUrl(scanId) : null;
}
