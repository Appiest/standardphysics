import type { ReactNode } from "react";

export function SheetField({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div className={`min-w-0 px-4 py-2 ${className}`}>
      <dt className="text-xs text-ink-muted">{label}</dt>
      <dd className="font-semibold tabular-nums">{children}</dd>
    </div>
  );
}

export const SHEET_GRID_CLASS = "grid gap-px bg-ink";
