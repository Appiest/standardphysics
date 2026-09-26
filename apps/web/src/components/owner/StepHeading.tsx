import type { ReactNode } from "react";

/** The one thing this step asks for, said once and large. */
export function StepHeading({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <header className="flex flex-col gap-2">
      <h1 className="heading-display text-3xl">{title}</h1>
      {children && <p className="text-pretty text-lg text-ink-muted">{children}</p>}
    </header>
  );
}

/** Where a step's one action lives: pinned to the bottom of the panel, above the phone's home bar. */
export function ActionBar({ children }: { children: ReactNode }) {
  return (
    <div className="sticky bottom-0 -mx-4 mt-auto flex flex-col gap-2 bg-paper/95 px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3 backdrop-blur-sm">
      {children}
    </div>
  );
}
