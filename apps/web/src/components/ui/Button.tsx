import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "quiet" | "danger" | "chip" | "inverse" | "choice";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-ink text-paper hover:bg-ink/85 px-5 py-3 text-base",
  quiet: "text-ink-muted hover:text-ink hover:bg-ink/5 px-3 py-2 text-sm",
  danger: "text-problem hover:bg-problem/10 px-3 py-2 text-sm",
  inverse: "text-paper hover:bg-paper/10 px-3 py-2 text-sm",
  choice: "bg-ink/[0.07] text-ink hover:bg-ink/[0.12] px-5 py-3 text-base",
  chip: "bg-sheet/95 text-ink shadow-float hover:bg-sheet shrink-0 whitespace-nowrap px-3 py-2 text-sm aria-pressed:bg-ink aria-pressed:text-paper",
};

/** The button look on its own, for a link that should read as a button. */
export function buttonClassName(variant: Variant = "quiet", squared = false): string {
  return `inline-flex items-center gap-2 ${squared ? "rounded-none" : "rounded-lg"} font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-40 ${VARIANTS[variant]}`;
}

export function Button({
  variant = "quiet",
  squared = false,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; squared?: boolean }) {
  return <button type="button" className={`${buttonClassName(variant, squared)} ${className}`} {...props} />;
}
