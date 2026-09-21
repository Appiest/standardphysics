import type { ReactNode } from "react";
import { zoneRowLetter } from "@/lib/schematic-brush/sheet";
import { ZoneBand } from "./ZoneBand";

interface SheetFrameProps {
  columns: number;
  rows: number;
  className?: string;
  children: ReactNode;
}

function sequence(count: number, label: (index: number) => string) {
  return Array.from({ length: count }, (_, index) => label(index));
}

export function SheetFrame({ columns, rows, className = "", children }: SheetFrameProps) {
  const columnLabels = sequence(columns, (index) => String(index + 1));
  const rowLabels = sequence(rows, zoneRowLetter);
  return (
    <div className={`grid grid-cols-[1.5rem_minmax(0,1fr)_1.5rem] grid-rows-[1.5rem_1fr_1.5rem] border-2 border-ink ${className}`}>
      <ZoneBand labels={columnLabels} orientation="horizontal" className="col-start-2 row-start-1" />
      <ZoneBand labels={rowLabels} orientation="vertical" className="col-start-1 row-start-2" />
      <ZoneBand labels={rowLabels} orientation="vertical" className="col-start-3 row-start-2" />
      <ZoneBand labels={columnLabels} orientation="horizontal" className="col-start-2 row-start-3" />
      <div className="relative col-start-2 row-start-2 min-h-0 border border-ink">{children}</div>
    </div>
  );
}
