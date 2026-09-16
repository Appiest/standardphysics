"use client";

import { ArrowClockwise } from "@phosphor-icons/react";
import { useEffect } from "react";
import { SheetFrame } from "@/components/blueprint/SheetFrame";
import { Button } from "@/components/ui/Button";

export default function WorkspaceError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="flex min-h-dvh px-3 py-3 sm:px-5 sm:py-5">
      <SheetFrame columns={8} rows={6} className="flex-1">
        <div className="flex h-full flex-col items-start justify-center gap-5 px-4 py-10 sm:px-8">
          <h1 className="heading-display text-4xl text-balance sm:text-5xl">This drawing did not load</h1>
          <p className="max-w-md text-ink-muted text-pretty">
            Your shop and its measurements are safe. The server that holds them did not answer just now.
          </p>
          <Button variant="primary" squared onClick={reset}>
            <ArrowClockwise size={20} weight="bold" aria-hidden />
            Try again
          </Button>
          {error.digest && <p className="measurement text-sm text-ink-faint">Reference {error.digest}</p>}
        </div>
      </SheetFrame>
    </main>
  );
}
