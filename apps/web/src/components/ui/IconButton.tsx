import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from "react";

type TooltipSide = "above" | "below";

const TOOLTIP_SIDE: Record<TooltipSide, string> = {
  above: "bottom-full mb-2",
  below: "top-full mt-2",
};

const ICON_CONTROL_CLASS =
  "group/icon relative grid size-9 shrink-0 place-items-center rounded-lg text-ink-muted transition-colors duration-150 hover:bg-ink/5 hover:text-ink active:scale-[0.96] aria-pressed:bg-ink aria-pressed:text-paper aria-disabled:cursor-not-allowed aria-disabled:opacity-40";

function Tooltip({ label, side }: { label: string; side: TooltipSide }) {
  return (
    <span
      aria-hidden
      className={`pointer-events-none absolute left-1/2 z-10 -translate-x-1/2 whitespace-nowrap rounded-md bg-ink px-2 py-1 text-xs font-medium text-paper opacity-0 transition-opacity duration-150 group-hover/icon:opacity-100 group-focus-visible/icon:opacity-100 ${TOOLTIP_SIDE[side]}`}
    >
      {label}
    </span>
  );
}

type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { label: string; tooltipSide?: TooltipSide; children: ReactNode };

export function IconButton({ label, tooltipSide = "above", className = "", children, ...props }: IconButtonProps) {
  return (
    <button type="button" aria-label={label} className={`${ICON_CONTROL_CLASS} ${className}`} {...props}>
      {children}
      <Tooltip label={label} side={tooltipSide} />
    </button>
  );
}

type IconLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & { label: string; tooltipSide?: TooltipSide; children: ReactNode };

export function IconLink({ label, tooltipSide = "above", className = "", children, ...props }: IconLinkProps) {
  return (
    <a aria-label={label} className={`${ICON_CONTROL_CLASS} ${className}`} {...props}>
      {children}
      <Tooltip label={label} side={tooltipSide} />
    </a>
  );
}
