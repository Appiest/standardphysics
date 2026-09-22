"use client";

import { Wheelchair } from "@phosphor-icons/react";
import { useCallback, useState } from "react";
import { Button } from "@/components/ui/Button";
import type { ApproachReport, ReachReport } from "@/types/contracts";

const STATUS_STYLE: Record<string, { label: string; className: string }> = {
  cleared: { label: "Approach clear", className: "bg-emerald-100 text-emerald-800" },
  clear: { label: "Approach clear", className: "bg-emerald-100 text-emerald-800" },
  blocked: { label: "Approach blocked", className: "bg-rose-100 text-rose-800" },
  needs_verification: { label: "Needs verification", className: "bg-amber-100 text-amber-800" },
};

const REACH_LABEL: Record<string, string> = {
  within_vertical_reach: "Height is within reach",
  beyond_vertical_reach: "Height is beyond reach",
  unmeasured: "Not measured",
  within_horizontal_reach: "Side reach: within what you measured",
  exceeded_horizontal_reach: "Side reach: farther than what you measured",
};

export function reachLine(reach: ReachReport): string {
  const horizontal = REACH_LABEL[reach.horizontal_status] ?? reach.horizontal_status;
  const vertical = REACH_LABEL[reach.vertical_status] ?? reach.vertical_status;
  const parts = [`${reach.occupant_title}: ${vertical}`];
  if (reach.horizontal_status !== "unmeasured") {
    parts.push(horizontal);
    if (reach.horizontal_reach_provenance) {
      parts.push(`(reach ${reach.horizontal_reach_inches?.toFixed(0) ?? "?"} in, ${reach.horizontal_reach_provenance})`);
    }
  }
  return parts.join(" • ");
}

/**
 * One measured journey to the selected target, on the owner's request.
 * An unmeasured horizontal reach stays visible as exactly that; the card is
 * a screening answer with its open questions, never a legal determination.
 */
export function ApproachCheck({ scanId, revision, nodeId, run }: {
  scanId: string;
  revision: number;
  nodeId: string;
  run: (body: { target_node_id: string }) => Promise<ApproachReport>;
}) {
  const [report, setReport] = useState<ApproachReport | null>(null);
  const [checking, setChecking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const check = useCallback(async () => {
    setChecking(true);
    setProblem(null);
    setReport(null);
    try {
      setReport(await run({ target_node_id: nodeId }));
    } catch (error) {
      const body = error as { status?: number; error?: string };
      setProblem(
        body.status === 409
          ? "This scan changed since you opened it. Reload, then check again."
          : body.status === 409 || body.status === 404
            ? body.error ?? "That object is not here anymore."
            : typeof (error as { error?: string }).error === "string"
              ? (error as { error: string }).error
              : "The approach check failed. Try again in a moment.",
      );
    } finally {
      setChecking(false);
    }
  }, [nodeId, run]);

  return (
    <div className="flex flex-col gap-2 border-t border-rule/60 pt-2">
      <Button variant="quiet" className="h-9 self-start px-2 text-xs text-accent" disabled={checking} onClick={() => void check()}>
        <Wheelchair size={14} aria-hidden />
        {checking ? "Measuring approach…" : "Check wheelchair approach"}
      </Button>
      {problem && <p role="alert" className="text-xs text-problem">{problem}</p>}
      {report && (
        <div role="status" className="flex flex-col gap-1 rounded-lg bg-rule/20 px-3 py-2">
          <div className="flex items-center gap-2">
            <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${(STATUS_STYLE[report.status] ?? STATUS_STYLE.needs_verification).className}`}>
              {(STATUS_STYLE[report.status] ?? STATUS_STYLE.needs_verification).label}
            </span>
            <span className="text-[10px] text-ink-muted">Screening only, not a legal determination</span>
          </div>
          {report.reaches.length > 0 && report.reaches.map((reach) => <p key={reach.occupant_title} className="text-[11px] text-ink">{reachLine(reach)}</p>)}
          {report.reaches.length === 0 && <p className="text-[11px] text-ink-muted">No reach numbers were measured for this object yet.</p>}
          {report.reasons.length > 0 && (
            <ul className="mt-1 list-disc pl-4 text-[11px] text-ink-muted">
              {report.reasons.map((reason) => <li key={reason}>{reason}</li>)}
            </ul>
          )}
          {report.unverified.length > 0 && (
            <p className="mt-1 text-[11px] text-ink-muted">Still open: {report.unverified.join(", ")}</p>
          )}
        </div>
      )}
    </div>
  );
}
