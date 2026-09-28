"use client";

import { ArrowsOutCardinal, Plus, Wheelchair } from "@phosphor-icons/react";
import type { ComponentType, ReactNode } from "react";
import { Button } from "@/components/ui/Button";
import { tellApp } from "@/lib/native-bridge";
import { useSeenOnce } from "@/lib/seen-once";

type IconType = ComponentType<{ size?: number; weight?: "regular" | "bold" | "fill"; "aria-hidden"?: boolean }>;

/** The shop tools, unlocked once the results first appear. Each opens straight into one task. */
export function ToolsPanel({ scanId, inApp, onPlan, onWheelchair }: { scanId: string; inApp: boolean; onPlan: () => void; onWheelchair: () => void }) {
  const [seen, markSeen] = useSeenOnce("sp_tools_unlocked");
  const addRoom = () => tellApp({ type: "addRoom", scanId });
  return (
    <section className="flex flex-col gap-3" aria-labelledby="tools-heading">
      {!seen && (
        <aside className="flex items-start gap-3 rounded-2xl bg-ink p-4 text-paper">
          <p className="flex-1 text-pretty">You unlocked shop tools. Try moving your furniture around and watch what it fixes.</p>
          <Button variant="inverse" className="shrink-0" onClick={markSeen}>Got it</Button>
        </aside>
      )}
      <h2 id="tools-heading" className="text-lg font-semibold">Shop tools</h2>
      <Tool Icon={ArrowsOutCardinal} title="Plan a layout" detail="Drag furniture to a new spot and see which problems it fixes.">
        <Button variant="choice" onClick={onPlan}>Start planning</Button>
      </Tool>
      <Tool Icon={Wheelchair} title="Wheelchair walk-through" detail="Roll along your customer path at the height of someone in a wheelchair, and see where it gets tight.">
        <Button variant="choice" onClick={onWheelchair}>Start the walk-through</Button>
      </Tool>
      <Tool Icon={Plus} title="Add another room" detail="Walk another room and it joins this shop.">
        {inApp ? <Button variant="choice" onClick={addRoom}>Walk another room</Button> : <p className="text-sm text-ink-muted">Open Standard Physics on your iPhone to walk it.</p>}
      </Tool>
    </section>
  );
}

function Tool({ Icon, title, detail, children }: { Icon: IconType; title: string; detail: string; children: ReactNode }) {
  return (
    <article className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 rounded-2xl bg-sheet p-4 shadow-float">
      <Icon size={24} weight="bold" aria-hidden />
      <div className="flex flex-col gap-1">
        <h3 className="font-semibold">{title}</h3>
        <p className="text-pretty text-sm text-ink-muted">{detail}</p>
      </div>
      <div className="col-start-2">{children}</div>
    </article>
  );
}
