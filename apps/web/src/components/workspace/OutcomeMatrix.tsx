"use client";

import { CaretDown, LinkSimple } from "@phosphor-icons/react";
import { useState } from "react";
import { LEGAL_REVIEW_LABEL, OUTCOME_LABEL, pendingFacts, rowsByItem, scopedSummary } from "@/lib/outcomes";
import type { Finding, ScopeManifest, ScopeRow } from "@/types/contracts";

const OUTCOME_STYLE: Record<ScopeRow["outcome"], string> = {
  satisfied: "bg-emerald-100 text-emerald-800",
  violation: "bg-rose-100 text-rose-800",
  needs_verification: "bg-amber-100 text-amber-800",
  not_applicable: "bg-ink/10 text-ink-faint",
  unobserved: "bg-sky-100 text-sky-800",
};

const APPLICABILITY_LABEL: Record<ScopeRow["applicability"], string> = {
  applicable: "Applies here",
  not_applicable: "Does not apply",
  unknown: "Applicability not known",
};

function sourceLink(findings: Finding[], requirementId: string) {
  const finding = findings.find((candidate) => candidate.check_id === requirementId);
  const citation = finding?.citation;
  if (!citation) return null;
  const text = `${citation.edition} ${citation.section}`;
  return citation.url ? (
    <a href={citation.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 underline decoration-rule underline-offset-2">
      {text} <LinkSimple size={12} aria-hidden />
    </a>
  ) : (
    <span>{text}</span>
  );
}

function RowBlock({ row, findings }: { row: ScopeRow; findings: Finding[] }) {
  return (
    <li className="flex flex-col gap-1 py-2 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${OUTCOME_STYLE[row.outcome]}`}>
          {OUTCOME_LABEL[row.outcome]}
        </span>
        {row.applicability === "not_applicable" && (
          <span className="rounded bg-ink/10 px-1.5 py-0.5 text-[10px] font-semibold text-ink-faint" title={row.applicability_reason ?? undefined}>
            {APPLICABILITY_LABEL[row.applicability]}
          </span>
        )}
        <span className="text-[10px] font-medium text-ink-faint">{LEGAL_REVIEW_LABEL[row.legal_review_status]}</span>
        <span className="measurement ml-auto text-[10px] text-ink-muted">{row.requirement_id}</span>
      </div>
      {row.applicability === "not_applicable" && row.applicability_reason && (
        <p className="text-[11px] text-ink-muted">{row.applicability_reason}</p>
      )}
      {row.reason && <p className="text-[11px] text-ink-muted">{row.reason}</p>}
      <p className="text-[11px] text-ink-faint">{sourceLink(findings, row.requirement_id) ?? "Source version not pinned"}</p>
    </li>
  );
}

export function OutcomeMatrix({ scope, findings }: { scope: ScopeManifest; findings: Finding[] }) {
  const [open, setOpen] = useState(true);
  const summary = scopedSummary(scope);
  const facts = pendingFacts(scope);
  const groups = rowsByItem(scope.rows);

  return (
    <section className="border-t border-rule/60 px-4 py-3">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between gap-2 rounded-md px-1 py-1 text-left"
      >
        <span className="flex items-center gap-2 text-sm font-semibold text-ink">
          Scoped checks
          <span className="text-xs font-normal text-ink-muted">version {scope.version}</span>
        </span>
        <CaretDown size={14} aria-hidden className={`transition-transform ${open ? "" : "-rotate-90"}`} />
      </button>

      <p className="mt-1 px-1 text-xs text-ink-muted">
        {summary} Outcomes are automated previews unless a row says a person reviewed it.
      </p>

      {facts.length > 0 && (
        <ul className="mt-2 list-disc rounded-lg bg-amber-50 py-2 pl-6 pr-3 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
          {facts.map((fact, index) => (
            <li key={index}>{fact}</li>
          ))}
        </ul>
      )}

      {open && (
        <div className="mt-3 flex flex-col gap-4">
          {groups.map((group) => (
            <section key={group.item_key}>
              <h3 className="px-1 text-xs font-semibold uppercase text-ink-muted">{group.label}</h3>
              <ul className="mt-1 divide-y divide-rule/60 rounded-lg border border-rule/60 bg-sheet/80 px-3">
                {group.rows.map((row, index) => (
                  <RowBlock key={`${row.requirement_id}-${index}`} row={row} findings={findings} />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </section>
  );
}
