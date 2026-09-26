import type { ReactNode } from "react";

export type Fact = { label: string; value: ReactNode };

/** Facts that matter as a key and value, one per row, instead of a sentence or a strip. */
export function FactList({ facts, className = "" }: { facts: Fact[]; className?: string }) {
  return (
    <dl className={`grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-ink-muted ${className}`}>
      {facts.map((fact) => (
        <div key={fact.label} className="contents">
          <dt>{fact.label}</dt>
          <dd className="text-ink">{fact.value}</dd>
        </div>
      ))}
    </dl>
  );
}
