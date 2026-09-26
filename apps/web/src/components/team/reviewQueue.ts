import type { PendingReview, ReviewAnswer } from "@/types/contracts";

export type ReviewOutcome = ReviewAnswer["outcome"];

export function reviewKey(review: PendingReview): string {
  return `${review.scan_id}/${review.request.id}`;
}

/** Where a decision on this photo goes: `PUT /api/team/reviews/{scan_id}/{request_id}`. */
export function reviewPath(review: PendingReview): string {
  return `/api/team/reviews/${encodeURIComponent(review.scan_id)}/${encodeURIComponent(review.request.id)}`;
}

export function stillWaiting(reviews: PendingReview[], decided: ReadonlySet<string>): PendingReview[] {
  return reviews.filter((review) => !decided.has(reviewKey(review)));
}

export function photosToCheckHeading(count: number): string {
  if (count === 0) return "No photos to check";
  return count === 1 ? "1 photo to check" : `${count} photos to check`;
}

const OUTCOME_WORDS: Record<ReviewOutcome, string> = { passes: "passing", problem: "a problem" };

export function decisionMessage(review: PendingReview, outcome: ReviewOutcome): string {
  return `Marked the photo from ${review.shop_name} as ${OUTCOME_WORDS[outcome]}.`;
}
