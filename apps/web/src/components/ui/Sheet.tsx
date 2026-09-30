"use client";

import { type ReactNode, useEffect, useRef } from "react";

/** A modal that rises from the bottom on a phone and sits centred on a wider screen. Escape and the backdrop close it. */
export function Sheet({ open, onClose, labelledBy, children }: { open: boolean; onClose: () => void; labelledBy: string; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    if (open && element && !element.open) element.showModal();
    if (!open && element?.open) element.close();
  }, [open]);

  return (
    <dialog ref={dialog} onClose={onClose} aria-labelledby={labelledBy}
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
      className="m-auto w-[min(100%-2rem,28rem)] rounded-2xl bg-sheet p-5 text-ink shadow-float backdrop:bg-ink/40 max-sm:mx-0 max-sm:mb-0 max-sm:mt-auto max-sm:w-full max-sm:max-w-none max-sm:rounded-b-none max-sm:pb-[max(1.25rem,env(safe-area-inset-bottom))]">
      {children}
    </dialog>
  );
}
