export type TooltipSide = "above" | "below";

const TOOLTIP_SIDE: Record<TooltipSide, string> = {
  above: "bottom-full mb-2",
  below: "top-full mt-2",
};

/**
 * Shown while its `group/icon` parent is hovered or keyboard-focused. Without an `id` it repeats the
 * control's own name and stays hidden from assistive tech; with one it is the control's description,
 * wired up through `aria-describedby`, and wraps to a readable width from the control's left edge.
 */
export function Tooltip({ label, side, id }: { label: string; side: TooltipSide; id?: string }) {
  const shape = id ? "left-0 w-64 whitespace-normal text-left" : "left-1/2 -translate-x-1/2 whitespace-nowrap";
  return (
    <span
      id={id}
      role={id ? "tooltip" : undefined}
      aria-hidden={id ? undefined : true}
      className={`pointer-events-none absolute z-10 rounded-md bg-ink px-2 py-1 text-xs font-medium text-paper opacity-0 transition-opacity duration-150 group-hover/icon:opacity-100 group-focus-visible/icon:opacity-100 ${shape} ${TOOLTIP_SIDE[side]}`}
    >
      {label}
    </span>
  );
}
