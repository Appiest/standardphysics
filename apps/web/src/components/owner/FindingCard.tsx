"use client";

import { ArrowsOutCardinal, CheckCircle, MinusCircle, Wrench } from "@phosphor-icons/react";
import type { ComponentType } from "react";
import { Button } from "@/components/ui/Button";
import { formatInches } from "@/lib/findings";
import { type ChecklistStatus, comparisonBars, MOVABLE_CHECKS } from "@/lib/owner-journey";
import type { Finding, SceneGraph } from "@/types/contracts";
import { SpotPlan } from "./SpotPlan";
import { StatusControl } from "./StatusControl";

export type CardActions = {
  onShow: (finding: Finding) => void;
  onStatus: (finding: Finding, status: ChecklistStatus) => void;
  onPlan: (finding: Finding) => void;
};

type IconType = ComponentType<{ size?: number; weight?: "regular" | "bold" | "fill"; className?: string; "aria-hidden"?: boolean }>;

/** How a settled item reads once the owner has said what they did about it. */
const SETTLED: Record<Exclude<ChecklistStatus, "to_do">, { Icon: IconType; tone: string; words: string }> = {
  done: { Icon: CheckCircle, tone: "text-pass", words: "Done" },
  not_doing: { Icon: MinusCircle, tone: "text-ink-muted", words: "Not doing" },
  needs_pro: { Icon: Wrench, tone: "text-attention", words: "Needs a pro" },
};

/**
 * One thing to fix. The drawing shows the spot, the two bars show how far off
 * it is, and the line under them says what to do. Once the owner starts fixing,
 * the card carries its status, and a settled card folds down to its title.
 */
export function FindingCard({ finding, scene, selected, status, fixing, saving, readOnly, actions }: {
  finding: Finding;
  scene: SceneGraph | null;
  selected: boolean;
  status: ChecklistStatus;
  fixing: boolean;
  saving: boolean;
  readOnly: boolean;
  actions: CardActions;
}) {
  const settled = status !== "to_do";
  return (
    <article className={`flex flex-col gap-4 rounded-2xl p-4 transition-shadow duration-150 ${settled ? "bg-ink/[0.04]" : "bg-sheet shadow-float"} ${selected ? "ring-2 ring-accent" : ""}`}>
      <button type="button" onClick={() => actions.onShow(finding)} aria-pressed={selected} className="flex flex-col gap-3 text-left">
        {!settled && scene && <SpotPlan scene={scene} finding={finding} />}
        <h2 className={`text-lg font-semibold leading-snug text-pretty ${settled ? "text-ink-muted" : ""}`}>{finding.title}</h2>
      </button>
      {settled ? <SettledMark status={status} /> : <Details finding={finding} readOnly={readOnly} onPlan={() => actions.onPlan(finding)} />}
      {fixing && <StatusControl name={`status-${finding.id}`} status={status} disabled={saving} onChange={(next) => actions.onStatus(finding, next)} />}
    </article>
  );
}

function SettledMark({ status }: { status: Exclude<ChecklistStatus, "to_do"> }) {
  const { Icon, tone, words } = SETTLED[status];
  return (
    <p className={`-mt-2 flex items-center gap-2 font-medium ${tone}`}>
      <Icon size={20} weight="fill" aria-hidden />
      {words}
    </p>
  );
}

function Details({ finding, readOnly, onPlan }: { finding: Finding; readOnly: boolean; onPlan: () => void }) {
  return (
    <>
      <Comparison finding={finding} />
      {finding.fix && <p className="border-l-2 border-accent pl-3 font-medium text-pretty">{finding.fix}</p>}
      {!readOnly && MOVABLE_CHECKS.has(finding.check_id) && (
        <Button variant="choice" className="self-start" onClick={onPlan}>
          <ArrowsOutCardinal size={18} weight="bold" aria-hidden />
          See a layout that fixes this
        </Button>
      )}
    </>
  );
}

/** The measurement and the number the standard needs, as two bars on one scale. */
function Comparison({ finding }: { finding: Finding }) {
  const { measured_inches: measured, required_inches: required } = finding;
  if (measured === null || required === null) return null;
  const bars = comparisonBars(measured, required);
  return (
    <dl className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2">
      <dt className="sr-only">Your shop</dt>
      <dd className="h-3 bg-problem" style={{ width: `${bars.measured * 100}%` }} aria-hidden />
      <dd className="measurement text-lg font-semibold text-problem">{formatInches(measured)}</dd>
      <dt className="sr-only">What&rsquo;s needed</dt>
      <dd className="h-3 border-2 border-dashed border-ink/50" style={{ width: `${bars.required * 100}%` }} aria-hidden />
      <dd className="measurement text-ink-muted">{formatInches(required)} needed</dd>
    </dl>
  );
}
