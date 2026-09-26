"use client";

import { ArrowClockwise, ArrowCounterClockwise, CheckCircle } from "@phosphor-icons/react";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import type { Arrangement } from "@/components/workspace/useArrangement";
import { ActionBar, StepHeading } from "./StepHeading";

const TURN_STEP_DEGREES = 15;

function planIntro(piece: string | null, moved: boolean): string {
  if (piece && !moved) return `Try dragging the ${piece}, outlined on the model, and watch the count below.`;
  return "Drag a piece of furniture on the model. We check the new layout each time you let go.";
}

function problemsLeft(arrangement: Arrangement, before: number): number {
  return arrangement.check ? arrangement.check.findings.filter((finding) => finding.outcome === "problem").length : before;
}

/** Plan a layout: drag pieces on the model, see what's left to fix, and keep the plan without changing the scan. */
export function PlanPanel({ arrangement, before, pieceName, onDone }: {
  arrangement: Arrangement;
  before: number;
  /** The piece in hand, or the one worth trying first. */
  pieceName: string | null;
  onDone: () => void;
}) {
  const [saved, setSaved] = useState(false);
  const piece = pieceName?.toLowerCase() ?? null;
  const turnable = arrangement.activeId !== null && piece !== null;
  return (
    <div className="flex min-h-full flex-col gap-6">
      <StepHeading title="Plan a layout">{planIntro(piece, arrangement.hasMoves)}</StepHeading>
      <PlanScore before={before} left={problemsLeft(arrangement, before)} checking={arrangement.checking} moved={arrangement.hasMoves} />
      {turnable && <TurnControls piece={piece} onTurn={(degrees) => arrangement.nudge(0, 0, degrees)} />}
      {arrangement.problem && <p role="alert" className="text-problem">{arrangement.problem}</p>}
      {saved && <SavedNote />}
      <PlanActions arrangement={arrangement} saved={saved} onSave={async () => setSaved(await arrangement.save())} onDone={onDone} />
    </div>
  );
}

function SavedNote() {
  return (
    <p role="status" className="flex items-center gap-2 font-medium text-pass">
      <CheckCircle size={20} weight="fill" aria-hidden />
      Saved. Once the real furniture moves, walk the shop again to update your results.
    </p>
  );
}

function PlanActions({ arrangement, saved, onSave, onDone }: { arrangement: Arrangement; saved: boolean; onSave: () => void; onDone: () => void }) {
  return (
    <ActionBar>
      <Button variant="primary" className="justify-center" disabled={!arrangement.canSave} onClick={onSave}>
        {arrangement.saving ? "Saving" : "Save this plan"}
      </Button>
      <div className="grid grid-cols-2 gap-2">
        <Button className="justify-center" disabled={!arrangement.hasMoves} onClick={arrangement.reset}>
          <ArrowCounterClockwise size={18} weight="bold" aria-hidden />
          Put it all back
        </Button>
        <Button className="justify-center" onClick={onDone}>{arrangement.hasMoves && !saved ? "Leave without saving" : "Done"}</Button>
      </div>
    </ActionBar>
  );
}

/** The count of things to fix with this layout, against the count the shop has now. */
function PlanScore({ before, left, checking, moved }: { before: number; left: number; checking: boolean; moved: boolean }) {
  const better = moved && left < before;
  return (
    <div aria-live="polite" className={`flex items-baseline gap-3 rounded-2xl p-4 ${better ? "bg-pass/10" : "bg-sheet shadow-float"}`}>
      <span className={`text-4xl font-semibold tabular-nums ${better ? "text-pass" : ""}`}>{checking ? "…" : left}</span>
      <ScoreWords before={before} left={left} checking={checking} moved={moved} />
    </div>
  );
}

function ScoreWords({ before, left, checking, moved }: { before: number; left: number; checking: boolean; moved: boolean }) {
  if (checking) return <p className="text-lg">Checking the layout</p>;
  return (
    <p className="text-lg">
      {left === 1 ? "thing" : "things"} to fix with this layout
      {moved && <span className="block text-base text-ink-muted">Your shop has {before} now</span>}
    </p>
  );
}

/** Turning a piece on a phone, where there's no keyboard: a step either way, checked like a drag. */
function TurnControls({ piece, onTurn }: { piece: string; onTurn: (degrees: number) => void }) {
  return (
    <div className="grid grid-cols-2 gap-2" role="group" aria-label={`Turn the ${piece}`}>
      <Button variant="choice" className="justify-center" onClick={() => onTurn(TURN_STEP_DEGREES)}>
        <ArrowCounterClockwise size={18} weight="bold" aria-hidden />
        Turn it left
      </Button>
      <Button variant="choice" className="justify-center" onClick={() => onTurn(-TURN_STEP_DEGREES)}>
        <ArrowClockwise size={18} weight="bold" aria-hidden />
        Turn it right
      </Button>
    </div>
  );
}
