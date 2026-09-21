import type { InputHTMLAttributes } from "react";

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: string;
}

/** A labelled input on the sheet.
 *
 * Square corners and an ink border, so a field reads as part of the drawing
 * rather than a control borrowed from somewhere else. */
export function Field({ label, hint, id, className = "", ...props }: FieldProps) {
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        aria-describedby={hintId}
        className={`border border-ink bg-sheet px-3 py-2.5 text-base placeholder:text-ink-faint ${className}`}
        {...props}
      />
      {hint && (
        <p id={hintId} className="text-sm text-ink-muted">
          {hint}
        </p>
      )}
    </div>
  );
}
