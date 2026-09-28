import type { Finding, OwnerRequest } from "@/types/contracts";

export interface StillToCheckItems {
  /** Photos and numbers the owner can send from the results. */
  sendable: OwnerRequest[];
  /** Spots the next walk has to go past more slowly, one card per spot however many checks rest on it. */
  lookAgain: Finding[];
}

export function stillToCheckItems(questions: Finding[], requests: OwnerRequest[]): StillToCheckItems {
  const byFinding = new Map(requests.map((request) => [request.finding_id, request]));
  const sendable = new Map<string, OwnerRequest>();
  const lookAgain: Finding[] = [];
  for (const finding of questions) {
    const request = byFinding.get(finding.id);
    if (isSendable(request)) sendable.set(request.id, request);
    else lookAgain.push(finding);
  }
  return { sendable: [...sendable.values()], lookAgain: onePerTitle(lookAgain) };
}

/** One card per thing to look at: the first of each title, in order. */
export function onePerTitle<T extends { title: string }>(items: T[]): T[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (seen.has(item.title)) return false;
    seen.add(item.title);
    return true;
  });
}

export function stillToCheckCount(items: StillToCheckItems): number {
  return items.sendable.length + items.lookAgain.length;
}

function isSendable(request: OwnerRequest | undefined): request is OwnerRequest {
  return request !== undefined && request.kind !== "another_look" && request.status !== "not_applicable";
}
