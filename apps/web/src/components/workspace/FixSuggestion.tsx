"use client";

import { CircleNotch, Lock, MagicWand } from "@phosphor-icons/react";
import { ProposalReview, ReviewFailure, SavedWishes } from "@/components/proposal/ProposalReview";
import { type ProposalReviewState, useProposalReview } from "@/components/proposal/useProposalReview";
import { Button } from "@/components/ui/Button";
import { formatInches } from "@/lib/findings";
import { inventoryLines } from "@/lib/inventory";
import { METERS_PER_INCH } from "@/lib/moves";
import type { Finding, NodeMove, OwnerWish, ProposalResult, SceneGraph } from "@/types/contracts";

type Props = { scanId: string; scene: SceneGraph; finding: Finding; wishes: OwnerWish[]; onTry: (moves: NodeMove[]) => void };

function moveLine(scene: SceneGraph, move: NodeMove): string {
  const label = scene.nodes.find((node) => node.id === move.node_id)?.label ?? "A piece";
  const inches = Math.hypot(move.delta_translation.x, move.delta_translation.y) / METERS_PER_INCH;
  const turn = Math.round(move.delta_rotation_z_degrees);
  const parts = [inches >= 0.25 ? `moves ${formatInches(inches)}` : null, turn ? `turns ${Math.abs(turn)}°` : null];
  return `${label} ${parts.filter(Boolean).join(" and ")}`;
}

type ResultProps = { result: ProposalResult; scene: SceneGraph; finding: Finding; review: ProposalReviewState; onTry: Props["onTry"] };

function Result({ result, scene, finding, review, onTry }: ResultProps) {
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
    <div className="mt-3 flex flex-col gap-3 rounded-lg bg-paper p-3">
      <p className="font-semibold" role="status">{result.message}</p>
      <ul className="flex flex-col gap-1">
        {proposal.moves.map((move) => <li key={move.node_id}>{moveLine(scene, move)}</li>)}
      </ul>
      {fixedCount > 0 && (
        <p className="flex items-center gap-2 text-sm text-ink-muted">
          <Lock size={14} weight="bold" aria-hidden />
          {fixedCount === 1 ? "1 fixed piece stays put" : `${fixedCount} fixed pieces stay put`}
        </p>
      )}
      <ul className="measurement text-sm text-ink-muted">
        {inventoryLines(proposal).map((line) => <li key={line}>{line}</li>)}
      </ul>
      <ProposalReview key={JSON.stringify(proposal.moves)} result={result} scene={scene} review={review} findingId={finding.id}
        onRelook={() => undefined}
        primary={<Button variant="primary" autoFocus onClick={() => onTry(proposal.moves)}>Try this layout</Button>} />
    </div>
  );
}

export function FixSuggestion({ scanId, scene, finding, wishes, onTry }: Props) {
  const review = useProposalReview(scanId, scene.revision, wishes);
  const result = review.result;
  return (
    <div className="mt-3 flex flex-col gap-2">
      {result ? (
        <Result result={result} scene={scene} finding={finding} review={review} onTry={onTry} />
      ) : (
        <Button variant="chip" className="self-start" onClick={() => review.propose(finding.id)} aria-disabled={review.looking}>
          {review.looking ? <CircleNotch size={16} className="animate-spin" aria-hidden /> : <MagicWand size={16} weight="bold" aria-hidden />}
          {review.looking ? "Looking for a layout" : "Find a layout that fixes this"}
        </Button>
      )}
      <SavedWishes review={review} findingId={result ? finding.id : null} onRelook={() => undefined} />
      <ReviewFailure review={review} />
    </div>
  );
}
