import type { ApiError, Report } from "@/types/contracts";

export type SharedReport = { kind: "report"; report: Report } | { kind: "gone"; message: string };

const GONE_WITHOUT_REASON = "This link has expired. Ask the shop for a new one.";

async function reasonGiven(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as Partial<ApiError>;
    return body.error || GONE_WITHOUT_REASON;
  } catch {
    return GONE_WITHOUT_REASON;
  }
}

/** What `GET /api/shared/{token}` answered: the report, or the API's own words for why there is none. */
export async function readSharedReport(response: Response): Promise<SharedReport> {
  if (response.ok) return { kind: "report", report: (await response.json()) as Report };
  if (response.status !== 404) throw new Error(`The shared report answered ${response.status}`);
  return { kind: "gone", message: await reasonGiven(response) };
}

/** "This link has expired. Ask the shop for a new one." becomes a heading and the sentence after it. */
export function headlineAndRest(message: string): { headline: string; rest: string } {
  const [first, ...others] = message.trim().split(/(?<=[.!?])\s+/);
  return { headline: first.replace(/\.$/, ""), rest: others.join(" ") };
}
