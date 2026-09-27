"use client";

import { CheckCircle, WarningCircle } from "@phosphor-icons/react";
import { useRouter } from "next/navigation";
import { type ReactNode, useRef, useState } from "react";
import type { PendingReview } from "@/types/contracts";
import { ReviewCard } from "./ReviewCard";
import { decisionMessage, photosToCheckHeading, reviewKey, stillWaiting, type ReviewOutcome } from "./reviewQueue";

type Decision = { message: string; outcome: ReviewOutcome };

const OUTCOME_MARK = {
  passes: <CheckCircle size={20} weight="fill" className="shrink-0 text-pass" aria-hidden />,
  problem: <WarningCircle size={20} weight="fill" className="shrink-0 text-problem" aria-hidden />,
} satisfies Record<ReviewOutcome, ReactNode>;

function LastDecision({ decision }: { decision: Decision | null }) {
  return (
    <p role="status" className="mt-3 flex items-center gap-2 text-ink empty:mt-0">
      {decision && OUTCOME_MARK[decision.outcome]}
      {decision?.message}
    </p>
  );
}

function EmptyQueue() {
  return <p className="mt-2 max-w-md text-lg text-ink-muted text-pretty">When an owner sends a photo, it shows up here.</p>;
}

/** Every photo waiting for a person on the team, oldest first, as the API orders them. */
export function ReviewQueueList({ reviews }: { reviews: PendingReview[] }) {
  const router = useRouter();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [decided, setDecided] = useState<ReadonlySet<string>>(new Set());
  const [lastDecision, setLastDecision] = useState<Decision | null>(null);
  const waiting = stillWaiting(reviews, decided);

  function recordDecision(review: PendingReview, outcome: ReviewOutcome) {
    setDecided((before) => new Set(before).add(reviewKey(review)));
    setLastDecision({ message: decisionMessage(review, outcome), outcome });
    headingRef.current?.focus();
    router.refresh();
  }

  return (
    <>
      <h1 ref={headingRef} tabIndex={-1} className="heading-display text-4xl focus-visible:outline-none sm:text-5xl">
        {photosToCheckHeading(waiting.length)}
      </h1>
      <LastDecision decision={lastDecision} />
      {waiting.length === 0 ? (
        <EmptyQueue />
      ) : (
        <div className="mt-6">
          {waiting.map((review) => (
            <ReviewCard key={reviewKey(review)} review={review} onDecided={(outcome) => recordDecision(review, outcome)} />
          ))}
        </div>
      )}
    </>
  );
}
