import type { InputHTMLAttributes, Ref, SelectHTMLAttributes } from "react";

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: string;
  /** What is wrong with the value; shown in place of the hint and announced with the field. */
  error?: string;
  ref?: Ref<HTMLInputElement>;
}

/** Square corners and an ink border, so a field reads as part of the drawing
 * rather than a control borrowed from somewhere else. */
const FIELD_LOOK = "border border-ink bg-sheet px-3 py-2.5 text-base";

/** A labelled input on the sheet. */
export function Field({ label, hint, error, id, className = "", ...props }: FieldProps) {
  const note = error ?? hint;
  const hintId = note ? `${id}-hint` : undefined;
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        aria-describedby={hintId}
        aria-invalid={error ? true : undefined}
        className={`${FIELD_LOOK} placeholder:text-ink-faint ${className}`}
        {...props}
      />
      {note && (
        <p id={hintId} className={`text-sm ${error ? "text-problem" : "text-ink-muted"}`}>
          {note}
        </p>
      )}
    </div>
  );
}

/** A labelled choice from a short list, drawn like `Field`. */
export function SelectField({ label, id, className = "", children, ...props }: SelectHTMLAttributes<HTMLSelectElement> & { label: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <select id={id} className={`${FIELD_LOOK} ${className}`} {...props}>
        {children}
      </select>
    </div>
  );
}
