"use client";

import { useEffect, useRef, type ReactNode } from "react";

/**
 * A button that opens a short list of less-used actions.
 *
 * Built on the native disclosure, so it opens with a keyboard, announces its
 * state and needs no script to work; the effect only closes it when a click
 * lands outside or Escape is pressed.
 */
export function Menu({ label, icon, children, align = "end", side = "below" }: {
  label: string;
  icon: ReactNode;
  children: ReactNode;
  align?: "start" | "end";
  side?: "below" | "above";
}) {
  const menu = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const close = (event: Event) => {
      const element = menu.current;
      if (!element?.open) return;
      const escaped = event instanceof KeyboardEvent && event.key === "Escape";
      if (escaped || (event.type === "pointerdown" && !element.contains(event.target as Node))) element.open = false;
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, []);
  return (
    <details ref={menu} className="relative">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-ink-muted hover:bg-ink/5 hover:text-ink [&::-webkit-details-marker]:hidden">
        {icon}
        {label}
      </summary>
      <div className={`absolute z-20 flex min-w-56 flex-col rounded-xl bg-sheet p-1 shadow-float ${align === "end" ? "right-0" : "left-0"} ${side === "below" ? "top-full mt-1" : "bottom-full mb-1"}`}>
        {children}
      </div>
    </details>
  );
}

/** The one look every row in a menu shares, whether it is a link or a button. */
export const MENU_ITEM = "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm font-medium text-ink hover:bg-ink/5";
