"use client";

import { Storefront } from "@phosphor-icons/react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { pieceName } from "@/lib/counter";
import { markCounter } from "@/lib/owner-client";
import type { SceneGraph } from "@/types/contracts";
import { ActionBar, StepHeading } from "./StepHeading";

/**
 * The first of the two checks: which piece customers pay or check in at.
 * The model shows the shop from above, and tapping a piece picks it.
 */
export function CounterStep({ scanId, scene, picked, onSkip }: { scanId: string; scene: SceneGraph; picked: string | null; onSkip: () => void }) {
  const router = useRouter();
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const node = scene.nodes.find((candidate) => candidate.id === picked) ?? null;

  const confirm = async () => {
    if (!node) return;
    setSaving(true);
    setProblem(null);
    try {
      await markCounter(scanId, scene.revision, node.id);
      router.refresh();
    } catch {
      setProblem("That didn't save. Try again in a moment.");
      setSaving(false);
    }
  };

  return (
    <div className="flex min-h-full flex-col gap-6">
      <StepHeading title="Tap where customers pay or check in" />
      <PickedPiece label={node ? pieceName(node.label) : null} />
      {problem && <p role="alert" className="text-problem">{problem}</p>}
      <ActionBar>
        <Button variant="primary" className="justify-center" disabled={!node || saving} onClick={confirm}>
          {saving ? "Saving" : "Customers pay here"}
        </Button>
        <Button className="justify-center" onClick={onSkip}>There isn&rsquo;t a counter</Button>
      </ActionBar>
    </div>
  );
}

function PickedPiece({ label }: { label: string | null }) {
  return (
    <p aria-live="polite" className={`flex items-center gap-3 rounded-2xl p-4 ${label ? "bg-sheet shadow-float" : "bg-ink/[0.04] text-ink-muted"}`}>
      <Storefront size={24} weight={label ? "fill" : "regular"} className={label ? "text-accent" : ""} aria-hidden />
      <span className="text-lg font-medium">{label ?? "Nothing picked yet"}</span>
    </p>
  );
}
