"use client";

import { Storefront } from "@phosphor-icons/react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { IconButton } from "@/components/ui/IconButton";
import { canBeCounter, isMarkedCounter, pieceName } from "@/lib/counter";
import { setCounter } from "@/lib/layout-client";
import type { SceneNode } from "@/types/contracts";

const REFRESH_AGAIN_MS = 3000;

function useCounterToggle(scanId: string, revision: number) {
  const router = useRouter();
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const toggle = async (node: SceneNode) => {
    setSaving(true);
    setProblem(null);
    try {
      await setCounter(scanId, revision, node.id, !isMarkedCounter(node));
      router.refresh();
      setTimeout(() => router.refresh(), REFRESH_AGAIN_MS);
    } catch {
      setProblem("That didn't save. Try again in a moment.");
    } finally {
      setSaving(false);
    }
  };
  return { saving, problem, toggle };
}

function CounterButton({ node, saving, onToggle }: { node: SceneNode; saving: boolean; onToggle: () => void }) {
  const marked = isMarkedCounter(node);
  return (
    <IconButton label={marked ? "Customers order here" : "Mark as the counter"} tooltipSide="below" aria-pressed={marked} disabled={saving} onClick={onToggle}>
      <Storefront size={18} weight={marked ? "fill" : "regular"} aria-hidden />
    </IconButton>
  );
}

/** The piece picked in the model, and the one thing a scan can't tell us about it: whether customers order there. */
export function PickedObject({ scanId, revision, label, node, editable }: {
  scanId: string;
  revision: number;
  label: string | null;
  node: SceneNode | null;
  editable: boolean;
}) {
  const { saving, problem, toggle } = useCounterToggle(scanId, revision);
  const offerCounter = editable && node !== null && canBeCounter(node);

  return (
    <div hidden={!label} className="absolute left-4 top-4 flex max-w-[calc(100%-2rem)] flex-col items-start gap-2">
      <div className="flex items-center gap-1 rounded-xl bg-sheet/95 p-1 shadow-float">
        <p aria-live="polite" className="px-2 text-sm font-medium text-ink">{label && pieceName(label)}</p>
        {offerCounter && <CounterButton node={node} saving={saving} onToggle={() => toggle(node)} />}
      </div>
      {problem && <p role="alert" className="rounded-lg bg-sheet px-3 py-2 text-sm text-problem">{problem}</p>}
    </div>
  );
}
