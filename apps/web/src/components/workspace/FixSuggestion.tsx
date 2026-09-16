"use client";

import { CircleNotch, Lock, MagicWand } from "@phosphor-icons/react";
import { useState } from "react";
import { ActivitySpinner } from "@/components/ui/ActivitySpinner";
import { Button } from "@/components/ui/Button";
import { formatInches } from "@/lib/findings";
import { inventoryLines } from "@/lib/inventory";
import { proposeFix } from "@/lib/layout-client";
import { METERS_PER_INCH } from "@/lib/moves";
import type { Finding, NodeMove, ProposalResult, SceneGraph } from "@/types/contracts";

type Props = { scanId: string; scene: SceneGraph; finding: Finding; onTry: (moves: NodeMove[]) => void };

function moveLine(scene: SceneGraph, move: NodeMove): string {
  const label = scene.nodes.find((node) => node.id === move.node_id)?.label ?? "A piece";
  const inches = Math.hypot(move.delta_translation.x, move.delta_translation.y) / METERS_PER_INCH;
  const turn = Math.round(move.delta_rotation_z_degrees);
  const parts = [inches >= 0.25 ? `moves ${formatInches(inches)}` : null, turn ? `turns ${Math.abs(turn)}°` : null];
  return `${label} ${parts.filter(Boolean).join(" and ")}`;
}

function Result({ result, scene, onTry }: { result: ProposalResult; scene: SceneGraph; onTry: Props["onTry"] }) {
  const proposal = result.proposal;
  if (!proposal) {
    return (
      <div className="mt-3 outline-none" role="status" tabIndex={-1} autoFocus>
        <p className="font-medium">{result.message}</p>
        {result.question && <p className="mt-1 text-ink-muted">{result.question}</p>}
      </div>
    );
  }
  const fixedCount = scene.nodes.filter((node) => node.kind === "object" && !node.movable).length;
  return (
    <div className="mt-3 rounded-lg bg-paper p-3">
      <p className="font-semibold" role="status">{result.message}</p>
      <ul className="mt-2 flex flex-col gap-1">
        {proposal.moves.map((move) => <li key={move.node_id}>{moveLine(scene, move)}</li>)}
      </ul>
      {fixedCount > 0 && (
        <p className="mt-2 flex items-center gap-2 text-sm text-ink-muted">
          <Lock size={14} weight="bold" aria-hidden />
          {fixedCount === 1 ? "1 fixed piece stays put" : `${fixedCount} fixed pieces stay put`}
        </p>
      )}
      <ul className="measurement mt-2 text-sm text-ink-muted">
        {inventoryLines(proposal).map((line) => <li key={line}>{line}</li>)}
      </ul>
      <Button variant="primary" className="mt-3" autoFocus onClick={() => onTry(proposal.moves)}>Try this layout</Button>
    </div>
  );
}

export function FixSuggestion({ scanId, scene, finding, onTry }: Props) {
  const [state, setState] = useState<"idle" | "looking" | "failed">("idle");
  const [result, setResult] = useState<ProposalResult | null>(null);

  async function find() {
    if (state === "looking") return;
    setState("looking");
    try {
      setResult(await proposeFix(scanId, scene.revision, [finding.id]));
      setState("idle");
    } catch {
      setState("failed");
    }
  }

  if (result) return <Result result={result} scene={scene} onTry={onTry} />;
  return (
    <div className="mt-3">
      <Button variant="chip" onClick={find} aria-disabled={state === "looking"}>
        {state === "looking" ? <CircleNotch size={16} className="animate-spin" aria-hidden /> : <MagicWand size={16} weight="bold" aria-hidden />}
        {state === "looking" ? "Looking for a layout" : "Find a layout that fixes this"}
      </Button>
      {state === "failed" && <ActivitySpinner className="mt-2" size={16} />}
    </div>
  );
}
