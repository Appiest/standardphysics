"use client";

import { CaretDown } from "@phosphor-icons/react";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { evidenceLanes, type LaneView } from "@/lib/evidence";
import type { EvidenceStatus, TextureStatus } from "@/types/contracts";

const TONE_STYLE: Record<string, string> = {
  ok: "bg-emerald-100 text-emerald-800",
  working: "bg-sky-100 text-sky-800",
  needs_action: "bg-amber-100 text-amber-800",
  failed: "bg-rose-100 text-rose-800",
};

function LaneRow({ lane, onAction }: { lane: LaneView; onAction?: (lane: LaneView) => void }) {
  return (
    <li className="flex flex-col gap-1 py-2 first:pt-0 last:pb-0">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-ink">{lane.name}</span>
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${TONE_STYLE[lane.tone]}`}>{lane.headline}</span>
      </div>
      {lane.detail && <p className="text-[11px] text-ink-muted">{lane.detail}</p>}
      {lane.action && onAction && (
        <Button variant="quiet" className="h-9 self-start px-2 text-xs text-accent" onClick={() => onAction(lane)}>
          {lane.action.label}
        </Button>
      )}
    </li>
  );
}

/**
 * The four processing lanes, each with its own state and its own remedy.
 * Nothing here spins forever: every pending lane names what would finish it.
 */
export function EvidencePanel({ status, textures, measured, onAction }: {
  status: EvidenceStatus | null;
  textures: TextureStatus | null;
  measured: boolean;
  onAction?: (lane: LaneView) => void;
}) {
  const [open, setOpen] = useState(false);
  const view = evidenceLanes(status, textures, measured);
  const pending = view.lanes.filter((lane) => lane.tone !== "ok" && lane.tone !== "working").length;

  if (view.allDone) {
    return (
      <p className="px-4 text-xs text-ink-muted">
        <span className="font-semibold text-ink">Scan processing</span> — geometry, photo evidence, recognition and textures all report done.
      </p>
    );
  }

  return (
    <section className="rounded-lg border border-rule/60 bg-sheet/95 p-3 text-sm shadow-sm">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between gap-2 rounded-md px-1 py-1 text-left"
      >
        <span className="text-sm font-semibold text-ink">Scan processing</span>
        <span className="flex items-center gap-2">
          {pending > 0 && (
            <span className="text-xs text-ink-muted">
              {pending} lane{pending === 1 ? "" : "s"} need{pending === 1 ? "s" : ""} attention
            </span>
          )}
          <CaretDown size={14} aria-hidden className={`transition-transform ${open ? "" : "-rotate-90"}`} />
        </span>
      </button>
      {open && (
        <ul className="mt-2 divide-y divide-rule/60">
          {view.lanes.map((lane) => (
            <LaneRow key={lane.id} lane={lane} onAction={onAction} />
          ))}
        </ul>
      )}
    </section>
  );
}
