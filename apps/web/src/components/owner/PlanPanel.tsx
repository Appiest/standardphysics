"use client";

import { ArrowClockwise, ArrowCounterClockwise, ArrowUUpLeft, ArrowsLeftRight, CheckCircle, CircleNotch, Hammer, Lock, MinusCircle, WarningCircle, Wrench } from "@phosphor-icons/react";
import { type ComponentType, type ReactNode, useEffect, useMemo, useRef } from "react";
import { Button } from "@/components/ui/Button";
import type { Arrangement } from "@/components/workspace/useArrangement";
import { blockedSentence } from "@/lib/blocked-copy";
import { formatInches } from "@/lib/findings";
import { type ChangeKind, changeTitle, type FindingChange, layoutChanges, unchangedProblems } from "@/lib/layout-changes";
import { justCleared, needsBuilding, openProblems, type StatusOf } from "@/lib/owner-decisions";
import type { ChecklistStatus } from "@/lib/owner-journey";
import type { Finding } from "@/types/contracts";
import { ActionBar } from "./StepHeading";

const TURN_STEP_DEGREES = 15;

/**
 * The findings as they stand, against the scanned layout's: the latest check,
 * else the scanned layout's own check, else what the shop was assessed with.
 * Problems the owner already handed to a contractor or set aside are not counted.
 */
function usePlanFindings(arrangement: Arrangement, scanned: Finding[], statusOf: StatusOf) {
  const before = arrangement.baseline?.findings ?? scanned;
  const { check } = arrangement;
  return useMemo(() => {
    const now = check?.findings ?? before;
    const changes = check ? layoutChanges(before, check.findings) : [];
    const still = unchangedProblems(now, changes);
    return {
      before: openProblems(before, statusOf).length,
      left: openProblems(now, statusOf).length,
      changes,
      movable: still.filter((finding) => !needsBuilding(finding)),
      building: now.filter((finding) => finding.outcome === "problem" && needsBuilding(finding)),
    };
  }, [before, check, statusOf]);
}

/** Calls `onAllClear` the moment the open count steps down to zero, counting from the scanned layout's, so a fixed layout that opens clear counts too. */
function useAllClear(before: number, left: number, checking: boolean, onAllClear: () => void) {
  const previous = useRef(before);
  useEffect(() => {
    if (justCleared(previous.current, left, checking)) onAllClear();
    if (!checking) previous.current = left;
  }, [left, checking, onAllClear]);
}

function pieceWords(pieceName: string | null, activeId: string | null): { piece: string | null; turnable: boolean } {
  const piece = pieceName?.toLowerCase() ?? null;
  return { piece, turnable: activeId !== null && piece !== null };
}

/** Try a layout: drag pieces on the plan, watch each check change as they move, and keep the plan without changing the scan. */
export function PlanPanel({ arrangement, scanned, pieceName, fixedNote, builtIn = false, review, fixPlan, statusOf, onDecide, onAllClear, onReset, onDone }: {
  arrangement: Arrangement;
  /** The findings the shop was assessed with, until the scanned layout's own check comes back. */
  scanned: Finding[];
  /** The piece in hand, or the one worth trying first. */
  pieceName: string | null;
  /** Whether the piece in hand is built in, like a counter, so moving it means construction. */
  builtIn?: boolean;
  /** What a proposed layout changes and a way to say what must stay, when the plan started from one. */
  review?: ReactNode;
  /** One press to have the layout model fix the plan as it stands. */
  fixPlan?: ReactNode;
  /** Said when the owner reaches for a piece that is built in. */
  fixedNote: string | null;
  /** What the owner decided about each problem: a contractor's job, set aside, or still to do. */
  statusOf: StatusOf;
  onDecide: (finding: Finding, status: ChecklistStatus) => void;
  /** Called when nothing is left to fix. */
  onAllClear: () => void;
  /** Puts every piece back where it was scanned, and drops the suggestion that moved them. */
  onReset: () => void;
  onDone: () => void;
}) {
  const { piece, turnable } = pieceWords(pieceName, arrangement.activeId);
  const { before, left, changes, movable, building } = usePlanFindings(arrangement, scanned, statusOf);
  useAllClear(before, left, arrangement.checking, onAllClear);
  return (
    <div className="flex min-h-full flex-col gap-5" data-check-ms={arrangement.latencyMs ?? undefined}>
      <PlanScore before={before} left={left} checking={arrangement.checking} moved={arrangement.hasMoves} />
      {!arrangement.hasMoves && <p className="-mt-2 text-ink-muted">Drag a piece on the plan.</p>}
      {movable.length + changes.filter((change) => change.kind === "new").length > 0 && fixPlan}
      {review}
      <Refusals arrangement={arrangement} fixedNote={fixedNote} />
      <Changes changes={changes} />
      <StillToFix findings={movable.filter((finding) => statusOf(finding.id) === "to_do")} />
      <BuildingWork findings={building} statusOf={statusOf} onDecide={onDecide} />
      {turnable && <PieceInHand piece={piece ?? ""} builtIn={builtIn} onTurn={(degrees) => arrangement.nudge(0, 0, degrees)} />}
      {arrangement.saved && <SavedNote />}
      <PlanActions arrangement={arrangement} onReset={onReset} onDone={onDone} />
    </div>
  );
}

/** Why the last drop didn't stick, or why a built-in piece didn't pick up. */
function Refusals({ arrangement, fixedNote }: { arrangement: Arrangement; fixedNote: string | null }) {
  const refused = arrangement.refused;
  if (refused.length === 0 && !fixedNote && !arrangement.problem) return null;
  return (
    <div role="alert" className="flex flex-col gap-1 rounded-2xl bg-problem/10 p-4">
      {refused.length > 0 && <p className="font-semibold">That spot doesn&rsquo;t work, so we put it back.</p>}
      {refused.map((blocked) => <p key={`${blocked.node_id}-${blocked.reason}`} className="text-pretty">{blockedSentence(blocked)}</p>)}
      {fixedNote && (
        <p className="flex items-center gap-2 text-pretty">
          <Lock size={18} weight="bold" aria-hidden className="shrink-0" />
          {fixedNote}
        </p>
      )}
      {arrangement.problem && <p className="text-pretty">{arrangement.problem}</p>}
    </div>
  );
}

type IconType = ComponentType<{ size?: number; weight?: "regular" | "bold" | "fill"; className?: string; "aria-hidden"?: boolean }>;

const CHANGE_LOOK: Record<ChangeKind, { Icon: IconType; tone: string; word: string; surface: string; title: string; weight: "bold" | "fill" }> = {
  new: { Icon: WarningCircle, tone: "text-problem", word: "New problem", surface: "bg-problem/10", title: "", weight: "fill" },
  cleared: { Icon: CheckCircle, tone: "text-pass", word: "Fixed", surface: "bg-pass/10", title: "text-ink-muted line-through decoration-1", weight: "fill" },
  changed: { Icon: ArrowsLeftRight, tone: "text-ink-muted", word: "Measurement moved", surface: "bg-sheet shadow-float", title: "", weight: "bold" },
};

function Changes({ changes }: { changes: FindingChange[] }) {
  if (changes.length === 0) return null;
  return (
    <section aria-label="What this layout changes" aria-live="polite">
      <ul className="flex flex-col gap-2">{changes.map((change) => <ChangeRow key={change.id} change={change} />)}</ul>
    </section>
  );
}

function ChangeRow({ change }: { change: FindingChange }) {
  const { Icon, tone, word, surface, title, weight } = CHANGE_LOOK[change.kind];
  return (
    <li className={`grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 rounded-2xl px-4 py-3 ${surface}`}>
      <Icon size={22} weight={weight} aria-hidden className={`mt-0.5 ${tone}`} />
      <div className="flex flex-col">
        <p className={`text-pretty font-medium ${title}`}><span className="sr-only">{word}: </span>{changeTitle(change)}</p>
        <MeasurementShift change={change} />
      </div>
    </li>
  );
}

type Shift = { was: number | null; now: number | null; needed: number | null };

function shiftOf(change: FindingChange): Shift {
  const describing = change.now ?? change.before;
  return {
    was: change.before?.measured_inches ?? null,
    now: change.now?.measured_inches ?? null,
    needed: describing?.required_inches ?? null,
  };
}

function Inches({ value, strong = false }: { value: number | null; strong?: boolean }) {
  if (value === null) return null;
  return <span className={`measurement ${strong ? "font-semibold text-ink" : ""}`}>{formatInches(value)}</span>;
}

/** "46 in to 29 in, 36 in needed": the number this layout moved, and what the standard asks for. */
function MeasurementShift({ change }: { change: FindingChange }) {
  const { was, now, needed } = shiftOf(change);
  if (was === null && now === null) return null;
  const both = was !== null && now !== null;
  return (
    <p className="text-sm text-ink-muted">
      <Inches value={was} />
      {both && " to "}
      <Inches value={now} strong />
      {needed !== null && <>, <Inches value={needed} /> needed</>}
    </p>
  );
}

function StillToFix({ findings }: { findings: Finding[] }) {
  if (findings.length === 0) return null;
  return (
    <section aria-labelledby="layout-still" className="flex flex-col gap-2">
      <h2 id="layout-still" className="text-lg font-semibold">Still to fix</h2>
      <ul className="flex flex-col gap-1">
        {findings.map((finding) => (
          <li key={finding.id} className="flex items-baseline justify-between gap-3">
            <span className="text-pretty">{finding.title}</span>
            {finding.measured_inches !== null && <span className="measurement shrink-0 text-sm text-problem">{formatInches(finding.measured_inches)}</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}

const DECIDED: Partial<Record<ChecklistStatus, { Icon: IconType; words: string; tone: string }>> = {
  needs_pro: { Icon: Wrench, words: "In the report for a contractor", tone: "text-attention" },
  not_doing: { Icon: MinusCircle, words: "Ignored", tone: "text-ink-muted" },
  done: { Icon: CheckCircle, words: "Done", tone: "text-pass" },
};

/** Problems no furniture move reaches, like a door frame: each goes in the report for a contractor, or is set aside. */
function BuildingWork({ findings, statusOf, onDecide }: { findings: Finding[]; statusOf: StatusOf; onDecide: (finding: Finding, status: ChecklistStatus) => void }) {
  if (findings.length === 0) return null;
  return (
    <section aria-labelledby="layout-building" className="flex flex-col gap-2">
      <h2 id="layout-building" className="flex items-center gap-2 text-lg font-semibold">
        <Hammer size={20} weight="bold" aria-hidden className="text-attention" />
        Needs building work
      </h2>
      <ul className="flex flex-col gap-2">
        {findings.map((finding) => <BuildingRow key={finding.id} finding={finding} status={statusOf(finding.id)} onDecide={onDecide} />)}
      </ul>
    </section>
  );
}

function BuildingRow({ finding, status, onDecide }: { finding: Finding; status: ChecklistStatus; onDecide: (finding: Finding, status: ChecklistStatus) => void }) {
  const decided = DECIDED[status];
  if (decided) {
    return (
      <li className="flex items-center gap-3 rounded-2xl bg-ink/[0.04] px-4 py-3">
        <decided.Icon size={20} weight="bold" aria-hidden className={`shrink-0 ${decided.tone}`} />
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium text-ink-muted">{finding.title}</p>
          <p className={`text-sm ${decided.tone}`}>{decided.words}</p>
        </div>
        <Button variant="quiet" onClick={() => onDecide(finding, "to_do")}>Undo</Button>
      </li>
    );
  }
  return (
    <li className="flex flex-col gap-3 rounded-2xl bg-sheet p-4 shadow-float">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-pretty font-semibold">{finding.title}</p>
        {finding.measured_inches !== null && <span className="measurement shrink-0 text-sm text-problem">{formatInches(finding.measured_inches)}</span>}
      </div>
      {finding.fix && <p className="border-l-2 border-accent pl-3 text-pretty">{finding.fix}</p>}
      <div className="grid grid-cols-[minmax(0,2fr)_minmax(0,1fr)] gap-2">
        <Button variant="choice" className="justify-center" onClick={() => onDecide(finding, "needs_pro")}>
          <Wrench size={18} weight="bold" aria-hidden />
          Add to report
        </Button>
        <Button className="justify-center" onClick={() => onDecide(finding, "not_doing")}>Ignore</Button>
      </div>
    </li>
  );
}

/** What can be done with the piece being moved, and what moving it involves when it is built in. */
function PieceInHand({ piece, builtIn, onTurn }: { piece: string; builtIn: boolean; onTurn: (degrees: number) => void }) {
  return (
    <>
      {builtIn && <BuiltInNote />}
      <TurnControls piece={piece} onTurn={onTurn} />
    </>
  );
}

function BuiltInNote() {
  return (
    <p className="flex items-start gap-2 text-pretty text-ink-muted">
      <Wrench size={20} weight="bold" className="mt-0.5 shrink-0" aria-hidden />
      Moving this built-in piece takes a contractor.
    </p>
  );
}

function SavedNote() {
  return (
    <p role="status" className="flex items-center gap-2 font-medium text-pass">
      <CheckCircle size={20} weight="fill" aria-hidden />
      Saved
    </p>
  );
}

function PlanActions({ arrangement, onReset, onDone }: { arrangement: Arrangement; onReset: () => void; onDone: () => void }) {
  return (
    <ActionBar>
      <Button variant="primary" className="justify-center" disabled={!arrangement.canSave} onClick={arrangement.save}>
        {arrangement.saving ? "Saving" : "Save this plan"}
      </Button>
      <div className="grid grid-cols-3 gap-2">
        <Button className="justify-center" disabled={!arrangement.canUndo} onClick={arrangement.undo}>
          <ArrowUUpLeft size={18} weight="bold" aria-hidden />
          Undo
        </Button>
        <Button className="justify-center" disabled={!arrangement.hasMoves} onClick={onReset}>
          <ArrowCounterClockwise size={18} weight="bold" aria-hidden />
          Put back
        </Button>
        <Button className="justify-center" onClick={onDone}>{arrangement.hasMoves && !arrangement.saved ? "Leave" : "Done"}</Button>
      </div>
    </ActionBar>
  );
}

/** The count of things to fix with this layout, against the count the shop has now. */
type Trend = "same" | "better" | "worse";

const TREND_LOOK: Record<Trend, { number: string; surface: string }> = {
  same: { number: "", surface: "bg-sheet shadow-float" },
  better: { number: "text-pass", surface: "bg-pass/10" },
  worse: { number: "text-problem", surface: "bg-problem/10" },
};

function trendOf(before: number, left: number, moved: boolean): Trend {
  if (!moved || left === before) return "same";
  return left < before ? "better" : "worse";
}

function PlanScore({ before, left, checking, moved }: { before: number; left: number; checking: boolean; moved: boolean }) {
  const look = TREND_LOOK[trendOf(before, left, moved)];
  return (
    <div aria-live="polite" className={`flex items-center gap-3 rounded-2xl p-4 transition-colors duration-150 ${look.surface}`}>
      <span className={`text-5xl font-semibold tabular-nums transition-opacity duration-150 ${look.number} ${checking ? "opacity-40" : ""}`}>{left}</span>
      <h1 className="text-lg font-medium">{left === 1 ? "problem" : "problems"} left</h1>
      <span className="ms-auto flex items-center gap-2 text-sm text-ink-muted">
        {checking && <CircleNotch size={16} className="motion-safe:animate-spin" aria-label="Checking" />}
        {moved && before !== left && <span className="tabular-nums">was {before}</span>}
      </span>
    </div>
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
