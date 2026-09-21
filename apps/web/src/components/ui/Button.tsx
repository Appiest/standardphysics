import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "quiet" | "danger" | "chip";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-ink text-paper hover:bg-ink/85 px-5 py-3 text-base",
  quiet: "text-ink-muted hover:text-ink hover:bg-ink/5 px-3 py-2 text-sm",
  danger: "text-problem hover:bg-problem/10 px-3 py-2 text-sm",
  chip: "bg-sheet/95 text-ink shadow-float hover:bg-sheet shrink-0 whitespace-nowrap px-3 py-2 text-sm aria-pressed:bg-ink aria-pressed:text-paper",
};

export function Button({
  variant = "quiet",
  squared = false,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; squared?: boolean }) {
  return (
    <button
      type="button"
      className={`inline-flex items-center gap-2 ${squared ? "rounded-none" : "rounded-lg"} font-medium transition-colors duration-150 ${VARIANTS[variant]} ${className}`}
      {...props}
    />
  );
}
