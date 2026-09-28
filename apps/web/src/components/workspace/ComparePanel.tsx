"use client";

import { ArrowRight } from "@phosphor-icons/react";
import { describeMovement, movements } from "@/lib/compare";
import { groupFindings } from "@/lib/findings";
import { allClearSentence, type CheckScope } from "@/lib/scan-status";
import type { Finding, SceneGraph } from "@/types/contracts";

export type Comparison = {
  before: SceneGraph;
  after: SceneGraph;
  beforeFindings: Finding[];
  afterFindings: Finding[];
  beforeLabel: string;
  afterLabel: string;
};

function Count({ findings }: { findings: Finding[] }) {
  const problems = groupFindings(findings).problems.length;
  return <span className={`measurement text-2xl font-bold ${problems === 0 ? "text-pass" : ""}`}>{problems}</span>;
}

function outcomeChanges(before: Finding[], after: Finding[]): { cleared: Finding[]; added: Finding[] } {
  const titles = (findings: Finding[]) => new Set(groupFindings(findings).problems.map((f) => f.title));
  const [was, now] = [titles(before), titles(after)];
  return {
    cleared: groupFindings(before).problems.filter((f) => !now.has(f.title)),
    added: groupFindings(after).problems.filter((f) => !was.has(f.title)),
  };
}

export function ComparePanel({ comparison, amount, onAmount, scope }: { comparison: Comparison; amount: number; onAmount: (value: number) => void; scope: CheckScope }) {
  const moved = movements(comparison.before, comparison.after);
  const { cleared, added } = outcomeChanges(comparison.beforeFindings, comparison.afterFindings);
  const allPass = groupFindings(comparison.afterFindings).problems.length === 0;

  return (
    <div className="flex flex-col gap-6 px-3">
      <div>
        <input
          type="range"
          min={0}
          max={100}
          value={Math.round(amount * 100)}
          onChange={(event) => onAmount(Number(event.target.value) / 100)}
          className="w-full accent-accent"
          aria-label={`Slide from ${comparison.beforeLabel.toLowerCase()} to ${comparison.afterLabel.toLowerCase()}`}
        />
        <div className="mt-1 flex justify-between text-sm font-medium">
          <button type="button" onClick={() => onAmount(0)} className="pressable rounded px-1 hover:bg-ink/5">{comparison.beforeLabel}</button>
          <button type="button" onClick={() => onAmount(1)} className="pressable rounded px-1 hover:bg-ink/5">{comparison.afterLabel}</button>
        </div>
      </div>

      <dl className="grid grid-cols-[1fr_auto_1fr] items-center gap-x-4 rounded-xl bg-sheet p-4 shadow-[0_1px_2px_rgb(27_28_30/0.08)]">
        <dt className="col-span-3 text-sm text-ink-muted">Things to fix</dt>
        <dd><Count findings={comparison.beforeFindings} /></dd>
        <dd><ArrowRight size={18} className="text-ink-faint" aria-hidden /></dd>
        <dd><Count findings={comparison.afterFindings} /></dd>
      </dl>

      {allPass && <p className="font-semibold text-pass">{allClearSentence(scope, "This layout passes everything we checked")}</p>}

      {cleared.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-ink-muted">Fixed by this layout</h2>
          <ul className="mt-2 flex flex-col gap-1">
            {cleared.map((finding) => <li key={finding.id} className="text-pass">{finding.title}</li>)}
          </ul>
        </section>
      )}
      {added.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-ink-muted">New with this layout</h2>
          <ul className="mt-2 flex flex-col gap-1">
            {added.map((finding) => <li key={finding.id} className="text-problem">{finding.title}</li>)}
          </ul>
        </section>
      )}

      <section>
        <h2 className="text-sm font-semibold text-ink-muted">What moved</h2>
        <ul className="mt-2 flex flex-col gap-1">
          {moved.map((movement) => <li key={movement.nodeId}>{describeMovement(movement)}</li>)}
        </ul>
      </section>
    </div>
  );
}
