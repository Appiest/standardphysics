import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from "react";
import { Tooltip, type TooltipSide } from "./Tooltip";

const ICON_CONTROL_CLASS =
  "group/icon relative grid size-9 shrink-0 place-items-center rounded-lg text-ink-muted transition-colors duration-150 hover:bg-ink/5 hover:text-ink active:scale-[0.96] aria-pressed:bg-ink aria-pressed:text-paper aria-disabled:cursor-not-allowed aria-disabled:opacity-40";

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
