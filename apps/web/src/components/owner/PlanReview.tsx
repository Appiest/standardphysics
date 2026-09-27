"use client";

import { ProposalReview, ReviewFailure, SavedWishes } from "@/components/proposal/ProposalReview";
import type { ProposalReviewState } from "@/components/proposal/useProposalReview";
import type { Finding, ProposalResult, SceneGraph } from "@/types/contracts";

type Props = {
  review: ProposalReviewState;
  scene: SceneGraph;
  finding: Finding | null;
  onRelook: (result: ProposalResult) => void;
  onPreview: (nodeId: string | null) => void;
};

/** On the owner's plan: what the suggested layout changes and fixes, and what they asked to keep. */
export function PlanReview({ review, scene, finding, onRelook, onPreview }: Props) {
  const result = review.result;
  if (!finding) return null;
  return (
    <section aria-label="Suggested layout" className="flex flex-col gap-3">
      {result && !result.proposal && (
        <div role="status">
          <p className="font-medium">{result.message}</p>
          {result.question && <p className="mt-1 text-ink-muted">{result.question}</p>}
        </div>
      )}
      {result?.proposal && (
        <ProposalReview key={JSON.stringify(result.proposal.moves)} result={result} scene={scene} review={review}
          findingId={finding.id} onRelook={onRelook} onPreview={onPreview} primary={null} />
      )}
      <SavedWishes review={review} findingId={finding.id} onRelook={onRelook} />
      <ReviewFailure review={review} />
    </section>
  );
}
