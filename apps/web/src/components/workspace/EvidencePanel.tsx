"use client";

import { WarningCircle } from "@phosphor-icons/react";
import { Button } from "@/components/ui/Button";
import { evidenceLanes, type LaneView } from "@/lib/evidence";
import type { EvidenceStatus, TextureStatus } from "@/types/contracts";

const NEEDS_THE_OWNER: LaneView["tone"][] = ["needs_action", "failed"];

function LaneRow({ lane, onAction }: { lane: LaneView; onAction?: (lane: LaneView) => void }) {
  return (
    <li className="flex flex-col gap-1">
      <p className="font-medium text-ink">{lane.headline}</p>
      {lane.detail && <p className="text-sm text-ink-muted">{lane.detail}</p>}
      {lane.action && onAction && (
        <Button variant="quiet" className="-ml-3 self-start" onClick={() => onAction(lane)}>
          {lane.action.label}
        </Button>
      )}
    </li>
  );
}

/**
 * What the scan still needs from the owner, and nothing else.
 *
 * Processing runs in four lanes, and a lane that finished or is still working
 * asks nothing of anyone, so it is not shown. A lane that failed or is waiting
 * on the owner appears with what to do about it.
 */
export function EvidencePanel({ status, textures, measured, onAction }: {
  status: EvidenceStatus | null;
  textures: TextureStatus | null;
  measured: boolean;
  onAction?: (lane: LaneView) => void;
}) {
  const waiting = evidenceLanes(status, textures, measured).lanes.filter((lane) => NEEDS_THE_OWNER.includes(lane.tone));
  if (waiting.length === 0) return null;
  return (
    <section className="flex gap-3 rounded-xl bg-attention/10 p-4">
      <WarningCircle size={22} weight="fill" className="shrink-0 text-attention" aria-hidden />
      <ul className="flex flex-col gap-3">
        {waiting.map((lane) => <LaneRow key={lane.id} lane={lane} onAction={onAction} />)}
      </ul>
    </section>
  );
}
