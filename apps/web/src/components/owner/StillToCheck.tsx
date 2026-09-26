import type { Finding, OwnerRequest } from "@/types/contracts";
import { RequestList } from "./RequestList";

/**
 * What the checks couldn't settle yet: photos and numbers the owner can still
 * send from here, and spots the next walk has to go past more slowly.
 */
export function StillToCheck({ scanId, questions, requests }: { scanId: string; questions: Finding[]; requests: OwnerRequest[] }) {
  if (questions.length === 0) return null;
  const byFinding = new Map(requests.map((request) => [request.finding_id, request]));
  const sendable = questions.map((finding) => byFinding.get(finding.id)).filter(isSendable);
  const others = questions.filter((finding) => !isSendable(byFinding.get(finding.id)));
  return (
    <section aria-labelledby="still-to-check" className="flex flex-col gap-3">
      <h2 id="still-to-check" className="text-lg font-semibold">Still to check</h2>
      {sendable.length > 0 && <RequestList scanId={scanId} requests={sendable} />}
      {others.length > 0 && (
        <ul className="flex flex-col gap-2">
          {others.map((finding) => (
            <li key={finding.id} className="rounded-2xl bg-ink/[0.04] p-4">
              <p className="font-medium">{finding.title}</p>
              <p className="mt-1 text-pretty text-sm text-ink-muted">{finding.detail}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function isSendable(request: OwnerRequest | undefined): request is OwnerRequest {
  return request !== undefined && request.kind !== "another_look" && request.status !== "not_applicable";
}
