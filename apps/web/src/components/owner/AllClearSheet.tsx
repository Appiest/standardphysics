"use client";

import { CheckCircle, FileText } from "@phosphor-icons/react";
import { Button } from "@/components/ui/Button";
import { Sheet } from "@/components/ui/Sheet";

/** Offered the moment nothing is left to fix: the report, with this layout kept in it, or back to the room. */
export function AllClearSheet({ open, opening, forContractor, onReport, onStay }: {
  open: boolean;
  /** The layout is being kept and the report is on its way. */
  opening: boolean;
  /** How many problems went in the report for a contractor. */
  forContractor: number;
  onReport: () => void;
  onStay: () => void;
}) {
  return (
    <Sheet open={open} onClose={onStay} labelledBy="all-clear-heading">
      <div className="flex flex-col items-center gap-2 pt-2 text-center">
        <CheckCircle size={56} weight="fill" className="text-pass" aria-hidden />
        <h2 id="all-clear-heading" className="heading-display text-3xl">0 problems left</h2>
        {forContractor > 0 && (
          <p className="text-ink-muted">{forContractor === 1 ? "1 is in the report for a contractor." : `${forContractor} are in the report for a contractor.`}</p>
        )}
      </div>
      <div className="mt-6 flex flex-col gap-2">
        <Button variant="primary" className="justify-center" disabled={opening} onClick={onReport}>
          <FileText size={20} weight="bold" aria-hidden />
          {opening ? "Opening the report" : "See the report"}
        </Button>
        <Button className="justify-center" onClick={onStay}>I want to check out the room</Button>
      </div>
    </Sheet>
  );
}
