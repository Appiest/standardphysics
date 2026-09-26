"use client";

import { ArrowSquareOut, CheckCircle, WarningCircle, type Icon } from "@phosphor-icons/react";
import { useId, useState } from "react";
import { FactList } from "@/components/report/FactList";
import { Button, buttonClassName } from "@/components/ui/Button";
import type { PendingReview, ReviewAnswer } from "@/types/contracts";
import { reviewPath, type ReviewOutcome } from "./reviewQueue";

const sentOn = new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", hour: "numeric", minute: "2-digit" });

const CHOICES: { outcome: ReviewOutcome; label: string; Icon: Icon }[] = [
  { outcome: "passes", label: "Mark as passing", Icon: CheckCircle },
  { outcome: "problem", label: "Mark as a problem", Icon: WarningCircle },
];

async function sendDecision(review: PendingReview, outcome: ReviewOutcome): Promise<boolean> {
  const body: ReviewAnswer = { outcome };
  try {
    const response = await fetch(reviewPath(review), {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    return response.ok;
  } catch {
    return false;
  }
}

function Photo({ url, shopName }: { url: string; shopName: string }) {
  return (
    <div className="flex flex-col items-start gap-2 md:col-start-1 md:row-span-2 md:row-start-1">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={url} alt={`The photo ${shopName} sent`} className="max-h-160 w-full rounded-lg bg-ink/5 object-contain" />
      <a href={url} target="_blank" rel="noreferrer" className={`-ms-3 ${buttonClassName("quiet")}`}>
        <ArrowSquareOut size={18} aria-hidden />
        Open full size
      </a>
    </div>
  );
}

function Choices({ saving, onChoose }: { saving: ReviewOutcome | null; onChoose: (outcome: ReviewOutcome) => void }) {
  return (
    <div className="flex w-full flex-col gap-3 sm:w-auto sm:flex-row">
      {CHOICES.map(({ outcome, label, Icon }) => (
        <Button
          key={outcome}
          variant="primary"
          squared
          disabled={saving !== null}
          onClick={() => onChoose(outcome)}
          className="justify-center disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Icon size={20} weight="bold" aria-hidden />
          {saving === outcome ? "Saving" : label}
        </Button>
      ))}
    </div>
  );
}

/** One photo an owner sent, with the two answers a person on the team can give it. */
export function ReviewCard({ review, onDecided }: { review: PendingReview; onDecided: (outcome: ReviewOutcome) => void }) {
  const headingId = useId();
  const [saving, setSaving] = useState<ReviewOutcome | null>(null);
  const [failed, setFailed] = useState(false);
  const { request, shop_name: shopName } = review;
  const answer = request.answer;

  async function choose(outcome: ReviewOutcome) {
    setSaving(outcome);
    setFailed(false);
    if (await sendDecision(review, outcome)) return onDecided(outcome);
    setSaving(null);
    setFailed(true);
  }

  return (
    <article
      aria-labelledby={headingId}
      className="grid gap-5 border-t border-rule py-8 md:grid-cols-[minmax(0,32rem)_minmax(0,1fr)] md:grid-rows-[auto_1fr] md:gap-x-10"
    >
      <div className="flex flex-col items-start gap-5 md:col-start-2 md:row-start-1">
        <div>
          <h2 id={headingId} className="text-xl font-semibold leading-snug text-pretty">{request.title}</h2>
          <p className="mt-2 text-ink-muted text-pretty">{request.detail}</p>
        </div>
        <FactList
          facts={[
            { label: "Shop", value: shopName },
            { label: "Sent", value: answer && <time dateTime={answer.answered_at} suppressHydrationWarning>{sentOn.format(new Date(answer.answered_at))}</time> },
          ]}
        />
      </div>
      {answer?.photo_url && <Photo url={answer.photo_url} shopName={shopName} />}
      <div className="flex flex-col items-start gap-3 md:col-start-2 md:row-start-2">
        <Choices saving={saving} onChoose={choose} />
        {failed && (
          <p role="alert" className="flex items-start gap-2 text-problem">
            <WarningCircle size={20} weight="fill" className="mt-0.5 shrink-0" aria-hidden />
            Unable to save your answer for this photo. Check your connection and try again.
          </p>
        )}
      </div>
    </article>
  );
}
