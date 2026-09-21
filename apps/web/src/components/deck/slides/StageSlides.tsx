"use client";

import type { ReactNode } from "react";
import { facts } from "@/lib/facts";
import { FinePrint, MaskedLines } from "../primitives";
import { ScanWipe } from "../ScanWipe";

function StageCaption({ children, source }: { children: ReactNode; source?: string }) {
  return (
    <div className="deck-gutter flex h-full flex-col items-start justify-end whitespace-nowrap">
      <h2 className="font-display text-headline font-extrabold">{children}</h2>
      {source && (
        <div className="mt-deck-hairline">
          <FinePrint delay={1.6}>{source}</FinePrint>
        </div>
      )}
    </div>
  );
}

export function ScanSlide() {
  return (
    <StageCaption>
      <ScanWipe delay={0.2} duration={1.2}>Sara walks</ScanWipe>
      <ScanWipe delay={0.4} duration={1.2}>through her shop</ScanWipe>
      <ScanWipe delay={0.6} duration={1.2}>with an iPhone.</ScanWipe>
    </StageCaption>
  );
}

export function FindSlide() {
  return (
    <StageCaption source={`${facts.counterHeightInLawsuit.source}; ${facts.accessibleCounterMaxHeight.source}`}>
      <MaskedLines
        lines={["The counter is", `${facts.counterHeightInLawsuit.value} inches high.`, `The limit is ${facts.accessibleCounterMaxHeight.value}.`]}
        delay={0.9}
      />
    </StageCaption>
  );
}

export function FixSlide() {
  return (
    <StageCaption source={facts.accessibleCounterMaxHeight.source}>
      <MaskedLines lines={["Move the card", "reader to the", "lower counter."]} delay={0.2} />
    </StageCaption>
  );
}
