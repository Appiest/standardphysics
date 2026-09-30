"use client";

import { ArrowsOutCardinal, CaretRight, Plus, Wheelchair } from "@phosphor-icons/react";
import type { ComponentType } from "react";
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
          <p className="flex-1 text-pretty">New: move your furniture and watch what it fixes.</p>
          <Button variant="inverse" className="shrink-0" onClick={markSeen}>Got it</Button>
        </aside>
      )}
      <h2 id="tools-heading" className="text-lg font-semibold">Shop tools</h2>
      <Tool Icon={ArrowsOutCardinal} title="Try moving furniture" onClick={onPlan} />
      <Tool Icon={Wheelchair} title="Roll through in a wheelchair" onClick={onWheelchair} />
      {inApp
        ? <Tool Icon={Plus} title="Walk another room" onClick={addRoom} />
        : <p className="text-sm text-ink-muted">Open Standard Physics on your iPhone to add another room.</p>}
    </section>
  );
}

function Tool({ Icon, title, onClick }: { Icon: IconType; title: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick}
      className="group flex w-full items-center gap-3 rounded-2xl bg-sheet p-4 text-start shadow-float pressable-wide hover:bg-ink/[0.03]">
      <Icon size={24} weight="bold" aria-hidden />
      <span className="flex-1 font-semibold">{title}</span>
      <CaretRight size={18} weight="bold" className="text-ink-muted transition-transform duration-150 group-hover:translate-x-0.5" aria-hidden />
    </button>
  );
}
