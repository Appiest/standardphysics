"use client";

import { CheckCircle, Circle, MinusCircle, Wrench } from "@phosphor-icons/react";
import type { ComponentType } from "react";
import type { ChecklistStatus } from "@/lib/owner-journey";

type IconType = ComponentType<{ size?: number; weight?: "regular" | "bold" | "fill"; "aria-hidden"?: boolean }>;

const OPTIONS: { status: ChecklistStatus; label: string; Icon: IconType }[] = [
  { status: "to_do", label: "To do", Icon: Circle },
  { status: "done", label: "Done", Icon: CheckCircle },
  { status: "not_doing", label: "Not doing", Icon: MinusCircle },
  { status: "needs_pro", label: "Needs a pro", Icon: Wrench },
];

/** The four things an owner can say about an item, as one group of radio buttons. */
export function StatusControl({ name, status, disabled, onChange }: {
  name: string;
  status: ChecklistStatus;
  disabled: boolean;
  onChange: (status: ChecklistStatus) => void;
}) {
  return (
    <fieldset className="grid grid-cols-2 gap-1 rounded-xl bg-ink/[0.05] p-1" disabled={disabled}>
      <legend className="sr-only">What you&rsquo;ve done about it</legend>
      {OPTIONS.map(({ status: option, label, Icon }) => {
        const chosen = option === status;
        return (
          <label key={option} className={`relative flex min-h-11 cursor-pointer items-center justify-center gap-1.5 rounded-lg px-2 text-sm font-medium transition-colors duration-150 has-focus-visible:outline-2 has-focus-visible:outline-accent ${chosen ? "bg-ink text-paper" : "text-ink-muted hover:bg-ink/5 hover:text-ink"}`}>
            <input type="radio" name={name} value={option} checked={chosen} onChange={() => onChange(option)} className="sr-only" />
            <Icon size={18} weight={chosen ? "fill" : "regular"} aria-hidden />
            {label}
          </label>
        );
      })}
    </fieldset>
  );
}
