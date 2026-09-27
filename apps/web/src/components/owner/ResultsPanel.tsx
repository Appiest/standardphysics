"use client";

import { type ReactNode, useMemo } from "react";
import { Button } from "@/components/ui/Button";
import type { ChecklistStatus } from "@/lib/owner-journey";
import { useSeenOnce } from "@/lib/seen-once";
import type { Finding, SceneGraph } from "@/types/contracts";
import { type CardActions, FindingCard } from "./FindingCard";
import { ActionBar } from "./StepHeading";

export type Row = { finding: Finding; status: ChecklistStatus };

const RESULTS_TIP = "sp_results_tip";

/**
 * The results and the checklist are one list. Before the owner starts fixing,
 * it counts what to fix; after, each card carries its status and the header
 * counts what's done.
 */
export function ResultsPanel({ rows, scene, selectedId, fixing, saving, actions, onStartFixing, readOnly, footer, stillToCheck, pending, children }: {
  rows: Row[];
  scene: SceneGraph | null;
  selectedId: string | null;
  fixing: boolean;
  saving: boolean;
  actions: CardActions;
  onStartFixing: () => void;
  readOnly: boolean;
  footer?: ReactNode;
  /** The questions the checks couldn't settle yet, shown after the list. */
  stillToCheck?: ReactNode;
  /** How many of those there are, so a shop with none to fix isn't called clear while some wait. */
  pending: number;
  children?: ReactNode;
}) {
  const [tipSeen, markTipSeen] = useSeenOnce(RESULTS_TIP);
  const cardActions = useMemo<CardActions>(() => ({
    ...actions,
    onShow: (finding) => { markTipSeen(); actions.onShow(finding); },
    onStatus: (finding, status) => { markTipSeen(); actions.onStatus(finding, status); },
  }), [actions, markTipSeen]);
  return (
    <div className="flex min-h-full flex-col gap-6">
      {fixing ? <Progress rows={rows} /> : <Count count={rows.length} pending={pending} />}
      {rows.length > 0 && !tipSeen && <FirstResultsTip onDismiss={markTipSeen} />}
      <ul className="flex flex-col gap-4">
        {rows.map(({ finding, status }) => (
          <li key={finding.id}>
            <FindingCard finding={finding} scene={scene} selected={finding.id === selectedId} status={status}
              fixing={fixing && !readOnly} saving={saving} readOnly={readOnly} actions={cardActions} />
          </li>
        ))}
      </ul>
      {stillToCheck}
      {footer}
      {children}
      {!fixing && !readOnly && rows.length > 0 && (
        <ActionBar>
          <Button variant="primary" className="justify-center" onClick={onStartFixing}>Start fixing</Button>
        </ActionBar>
      )}
    </div>
  );
}

/** One heading that says what it counts. The number carries the red, and nothing else does. */
function Count({ count, pending }: { count: number; pending: number }) {
  if (count === 0) {
    return <h1 className="heading-display text-3xl">{pending === 0 ? "Nothing to fix" : "Nothing to fix so far"}</h1>;
  }
  return (
    <h1 className="heading-display text-3xl">
      <span className="text-problem">{count}</span> {count === 1 ? "thing to fix" : "things to fix"}
    </h1>
  );
}

/** How far through the list the owner is: one segment per item, filled once it has a status. */
function Progress({ rows }: { rows: Row[] }) {
  const done = rows.filter((row) => row.status !== "to_do").length;
  return (
    <header className="flex flex-col gap-3">
      <h1 className="heading-display text-3xl">{done} of {rows.length} done</h1>
      <div className="flex gap-1" aria-hidden>
        {rows.map(({ finding, status }) => (
          <span key={finding.id} className={`h-1.5 flex-1 rounded-full transition-colors duration-300 ${status === "to_do" ? "bg-ink/10" : "bg-pass"}`} />
        ))}
      </div>
    </header>
  );
}

function FirstResultsTip({ onDismiss }: { onDismiss: () => void }) {
  return (
    <aside className="flex items-start gap-3 rounded-2xl bg-ink p-4 text-paper">
      <p className="flex-1 text-pretty">A red bar is how far a spot misses the ADA number. Tap a card to see where it is.</p>
      <Button variant="inverse" className="shrink-0" onClick={onDismiss}>Got it</Button>
    </aside>
  );
}
