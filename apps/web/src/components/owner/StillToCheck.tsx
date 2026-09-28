import type { StillToCheckItems } from "@/lib/still-to-check";
import { RequestMenu } from "./RequestList";

/**
 * What the checks couldn't settle yet: photos and numbers the owner can still
 * send from here, and spots the next walk has to go past more slowly.
 */
export function StillToCheck({ scanId, items }: { scanId: string; items: StillToCheckItems }) {
  const { sendable, lookAgain } = items;
  if (sendable.length === 0 && lookAgain.length === 0) return null;
  return (
    <div className="flex flex-col gap-3">
      {sendable.length > 0 && <RequestMenu scanId={scanId} requests={sendable} />}
      {lookAgain.length > 0 && (
        <ul className="flex flex-col gap-2">
          {lookAgain.map((finding) => (
            <li key={finding.id} className="rounded-2xl bg-ink/[0.04] p-4">
              <p className="font-medium">{finding.title}</p>
              <p className="mt-1 text-pretty text-sm text-ink-muted">{finding.detail}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
