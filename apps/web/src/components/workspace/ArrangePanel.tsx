"use client";

import { ArrowCounterClockwise, CircleNotch } from "@phosphor-icons/react";
import { blockedSentence } from "@/lib/blocked-copy";
import { groupFindings } from "@/lib/findings";
import { allClearSentence, type CheckScope } from "@/lib/scan-status";
import type { Finding } from "@/types/contracts";
import { Button } from "@/components/ui/Button";
import type { Arrangement } from "./useArrangement";
import { FindingsList } from "./FindingsList";
import { SuggestRearrangement } from "./SuggestRearrangement";

function Status({ arrangement, problems, scope }: { arrangement: Arrangement; problems: number; scope: CheckScope }) {
  if (arrangement.checking) {
    return (
      <p className="flex items-center gap-2 font-medium" role="status">
        <CircleNotch size={18} className="animate-spin text-ink-muted" aria-hidden />
        Checking the new layout
      </p>
    );
  }
  if (!arrangement.check) {
    return <p className="text-ink-muted">Drag a table or case to move it. With a keyboard, press R to turn the one you picked.</p>;
  }
  if (problems === 0 && arrangement.check.blocked.length === 0) {
    return <p className="font-semibold text-pass" role="status">{allClearSentence(scope, "This layout passes everything we checked")}</p>;
  }
  return (
    <p className="font-semibold" role="status">
      {problems === 1 ? "1 thing still to fix" : `${problems} things still to fix`}
    </p>
  );
}

function LayoutActions({ arrangement }: { arrangement: Arrangement }) {
  return (
    <div className="mt-4 flex flex-wrap gap-2">
      <Button variant="primary" onClick={arrangement.save} disabled={!arrangement.canSave} className="disabled:opacity-40">
        {arrangement.saving ? "Saving" : "Save this layout"}
      </Button>
      {arrangement.hasMoves && (
        <Button onClick={arrangement.reset} disabled={arrangement.puttingBack}>
          <ArrowCounterClockwise size={16} weight="bold" aria-hidden />
          {arrangement.puttingBack ? "Putting everything back" : "Put everything back"}
        </Button>
      )}
    </div>
  );
}

type Props = { arrangement: Arrangement; fallbackFindings: Finding[]; scope: CheckScope; scanId: string; revision: number };

export function ArrangePanel({ arrangement, fallbackFindings, scope, scanId, revision }: Props) {
  const findings = arrangement.check?.findings ?? fallbackFindings;
  const groups = groupFindings(findings);

  return (
    <div className="flex flex-col gap-5">
      <div className="px-3">
        <Status arrangement={arrangement} problems={groups.problems.length} scope={scope} />
        {[...arrangement.refused, ...(arrangement.check?.blocked ?? [])].map((blocked) => (
          <p key={`${blocked.node_id}-${blocked.reason}`} className="mt-2 flex gap-2 text-problem">
            <span className="mt-2 size-2 shrink-0 rounded-full bg-problem" aria-hidden />
            {blockedSentence(blocked)}
          </p>
        ))}
        {arrangement.problem && <p className="mt-2 text-problem">{arrangement.problem}</p>}
        <div className="mt-4">
          <SuggestRearrangement key={revision} scanId={scanId} revision={revision} showingSuggestion={arrangement.source === "suggestion"} onSuggested={arrangement.loadSuggestion} />
        </div>
        <LayoutActions arrangement={arrangement} />
      </div>
      <FindingsList groups={groups} selectedId={null} onSelect={() => undefined} />
    </div>
  );
}
