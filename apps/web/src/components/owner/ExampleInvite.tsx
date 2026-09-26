"use client";

import { Scan } from "@phosphor-icons/react";
import { useSyncExternalStore } from "react";
import { Button } from "@/components/ui/Button";
import { startWalk } from "@/lib/native-bridge";

function canWalkHere(): boolean {
  const handlers = (window as unknown as { webkit?: { messageHandlers?: Record<string, unknown> } }).webkit?.messageHandlers;
  return Boolean(handlers?.nativeCapture);
}

/** Under the example's results: the same list for your own shop is one walk away. */
export function ExampleInvite() {
  const inApp = useSyncExternalStore(() => () => {}, canWalkHere, () => false);
  return (
    <section aria-labelledby="own-shop" className="flex flex-col gap-3 rounded-2xl bg-sheet p-4 shadow-float">
      <h2 id="own-shop" className="text-lg font-semibold">Measure your own shop</h2>
      <p className="text-pretty text-ink-muted">Walk once around your shop with Standard Physics on an iPhone Pro, and you&rsquo;ll get a list like this one for it.</p>
      {inApp && (
        <Button variant="primary" className="justify-center" onClick={() => startWalk()}>
          <Scan size={20} weight="bold" aria-hidden />
          Measure your shop
        </Button>
      )}
    </section>
  );
}
