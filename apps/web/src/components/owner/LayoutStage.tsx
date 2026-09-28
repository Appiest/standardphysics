"use client";

import { Cube, SquaresFour } from "@phosphor-icons/react";
import { useMemo } from "react";
import { Button } from "@/components/ui/Button";
import type { Arrangement } from "@/components/workspace/useArrangement";
import type { SceneGraph } from "@/types/contracts";
import { LayoutPlan } from "./LayoutPlan";
import type { LayoutView, TryLayout } from "./useTryLayout";

const VIEWS: { id: LayoutView; words: string; Icon: typeof Cube }[] = [
  { id: "plan", words: "From above", Icon: SquaresFour },
  { id: "model", words: "In 3D", Icon: Cube },
];

/**
 * The plan drawn over the 3D model while a layout is being tried. Both draw the
 * same moved layout, so switching views shows the same pieces where they were
 * left; the model stays mounted under the plan so switching is instant.
 */
export function LayoutStage({ arrangement, scanned, trial, pointedIds }: {
  arrangement: Arrangement;
  scanned: SceneGraph;
  trial: TryLayout;
  /** Pieces the owner is pointing at in the found list. */
  pointedIds: Set<string>;
}) {
  const { drag, drop } = arrangement;
  const handlers = useMemo(() => ({
    onGrab: trial.onGrab,
    onDrag: drag,
    onDrop: () => drop(),
  }), [trial.onGrab, drag, drop]);
  return (
    <>
      {trial.view === "plan" && (
        <div className="absolute inset-0">
          <LayoutPlan
            shown={arrangement.shown} scanned={scanned}
            activeId={arrangement.activeId} blockedIds={arrangement.blockedIds} pointedIds={pointedIds} movedIds={trial.movedIds}
            problems={trial.drawn.problems} cleared={trial.drawn.cleared}
            onFixedTap={trial.onFixedTap} onKey={trial.onKey} {...handlers}
          />
        </div>
      )}
      <div role="group" aria-label="How to show the shop" className="absolute right-3 top-3 flex gap-1 rounded-xl bg-sheet/95 p-1 shadow-float">
        {VIEWS.map(({ id, words, Icon }) => (
          <Button key={id} aria-pressed={trial.view === id} className="aria-pressed:bg-ink aria-pressed:text-paper" onClick={() => trial.setView(id)}>
            <Icon size={16} weight="bold" aria-hidden />
            {words}
          </Button>
        ))}
      </div>
    </>
  );
}
