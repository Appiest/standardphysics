"use client";

import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/Button";
import { VirtualButton } from "@/components/workspace/WheelchairHud";
import { ActionBar, StepHeading } from "./StepHeading";

/** The wheelchair walk-through, beside the model: what it is and how to leave it. */
export function WheelchairPanel({ onDone }: { onDone: () => void }) {
  return (
    <div className="flex min-h-full flex-col gap-6">
      <StepHeading title="Wheelchair walk-through">
        You&rsquo;re at the front door, seated at the height of someone in a wheelchair. Hold an arrow to roll along the dashed line to each stop.
      </StepHeading>
      <ActionBar>
        <Button variant="primary" className="justify-center" onClick={onDone}>Done</Button>
      </ActionBar>
    </div>
  );
}

function PadButton({ code, label, children }: { code: string; label: string; children: ReactNode }) {
  return <VirtualButton code={code} label={label} large>{children}</VirtualButton>;
}

/** Four arrows on the model, big enough for a thumb. A wheelchair turns and rolls; it doesn't slide sideways. */
export function DrivingPad() {
  return (
    <div className="absolute inset-x-0 bottom-4 flex justify-center" role="group" aria-label="Roll the wheelchair">
      <div className="grid grid-cols-3 gap-2">
        <span />
        <PadButton code="KeyW" label="Roll forward"><ArrowUp size={24} weight="bold" aria-hidden /></PadButton>
        <span />
        <PadButton code="KeyA" label="Turn left"><ArrowLeft size={24} weight="bold" aria-hidden /></PadButton>
        <PadButton code="KeyS" label="Roll back"><ArrowDown size={24} weight="bold" aria-hidden /></PadButton>
        <PadButton code="KeyD" label="Turn right"><ArrowRight size={24} weight="bold" aria-hidden /></PadButton>
      </div>
    </div>
  );
}
