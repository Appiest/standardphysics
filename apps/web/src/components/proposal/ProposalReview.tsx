"use client";

import { CaretDown, CheckCircle, CircleNotch, WarningCircle } from "@phosphor-icons/react";
import { type FormEvent, type ReactNode, useId, useState } from "react";
import { Button } from "@/components/ui/Button";
import { type KeepChoice, keepChoices, withoutWish, withWishes, wishKey } from "@/lib/owner-wishes";
import type { ProposalExplanation, ProposalResult, SceneGraph } from "@/types/contracts";
import type { ProposalReviewState } from "./useProposalReview";

type Relook = (result: ProposalResult) => void;

function Explanation({ explanation }: { explanation: ProposalExplanation }) {
  return (
    <div className="flex flex-col gap-2 text-sm">
      {explanation.fixed.map((line) => <p key={line}>{line}</p>)}
      {explanation.kept.length > 0 && (
        <ul className="flex flex-col gap-1">
          {explanation.kept.map((line) => (
            <li key={line} className="flex items-start gap-2">
              <CheckCircle size={16} weight="fill" className="mt-0.5 shrink-0 text-pass" aria-hidden />
              <span><span className="sr-only">Kept: </span>{line}</span>
            </li>
          ))}
        </ul>
      )}
      {explanation.bent.length > 0 && (
        <ul className="flex flex-col gap-1">
          {explanation.bent.map((item) => (
            <li key={item.text} className="flex items-start gap-2">
              <WarningCircle size={16} weight="fill" className="mt-0.5 shrink-0 text-attention" aria-hidden />
              <span><span className="font-medium">Changes:</span> {item.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

type Preview = (nodeId: string | null) => void;
type ChooseProps = {
  choices: KeepChoice[];
  busy: boolean;
  onLookAgain: (wishes: KeepChoice["wish"][]) => void;
  onCancel: () => void;
  onPreview?: Preview;
};

function ChooseWhatStays({ choices, busy, onLookAgain, onCancel, onPreview }: ChooseProps) {
  const formId = useId();
  const [picked, setPicked] = useState(() => new Set(choices.filter((choice) => choice.bent).map((choice) => choice.key)));
  const [missing, setMissing] = useState(false);

  function toggle(key: string) {
    const next = new Set(picked);
    if (next.has(key)) next.delete(key); else next.add(key);
    setPicked(next);
    setMissing(false);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (picked.size === 0) return setMissing(true);
    onLookAgain(choices.filter((choice) => picked.has(choice.key)).map((choice) => choice.wish));
  }

  return (
    <form onSubmit={submit} className="rounded-lg bg-sheet p-3 shadow-float">
      <fieldset aria-describedby={missing ? `${formId}-missing` : undefined}>
        <legend className="font-semibold">What should stay as it is?</legend>
        <div className="mt-2 flex flex-col">
          {choices.map((choice) => (
            <label key={choice.key} htmlFor={`${formId}-${choice.key}`}
              className="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-2 hover:bg-ink/[0.04]"
              onMouseEnter={() => onPreview?.(choice.wish.node_id)}>
              <input id={`${formId}-${choice.key}`} type="checkbox" className="size-4 accent-ink" checked={picked.has(choice.key)}
                onChange={() => toggle(choice.key)} onFocus={() => onPreview?.(choice.wish.node_id)} />
              {choice.label}
            </label>
          ))}
        </div>
      </fieldset>
      {missing && <p id={`${formId}-missing`} className="mt-2 text-sm text-problem">Pick at least one thing to keep, so we look for a different layout.</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button type="submit" variant="primary" aria-disabled={busy}>
          {busy && <CircleNotch size={16} className="animate-spin" aria-hidden />}
          Find another layout
        </Button>
        <Button variant="quiet" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}

type ReviewProps = {
  result: ProposalResult;
  scene: SceneGraph;
  review: ProposalReviewState;
  findingId: string;
  /** The surface's own way to take the layout, shown beside "Choose what stays". */
  primary: ReactNode;
  onRelook: Relook;
  /** Outlines the piece a choice is about, where the surface shows the room. */
  onPreview?: Preview;
};

/** What a proposal changes and fixes, and a way for the owner to say what must stay before looking again. */
export function ProposalReview({ result, scene, review, findingId, primary, onRelook, onPreview }: ReviewProps) {
  const [choosing, setChoosing] = useState(false);
  const lookAgain = (added: KeepChoice["wish"][]) => review.keep(withWishes(review.wishes, added), findingId, onRelook);
  return (
    <div className="flex flex-col gap-3">
      {result.explanation && <Explanation explanation={result.explanation} />}
      {choosing ? (
        <ChooseWhatStays choices={keepChoices(result, scene)} busy={review.looking} onLookAgain={lookAgain}
          onCancel={() => setChoosing(false)} onPreview={onPreview} />
      ) : (
        <div className="flex flex-wrap gap-2">
          {primary}
          <Button variant="choice" onClick={() => setChoosing(true)}>Choose what stays</Button>
        </div>
      )}
    </div>
  );
}

/** Everything the owner asked to keep, one tap from removing each. */
export function SavedWishes({ review, findingId, onRelook }: { review: ProposalReviewState; findingId: string | null; onRelook: Relook }) {
  if (review.wishes.length === 0) return null;
  const count = review.wishes.length;
  return (
    <details className="group">
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 rounded-lg px-2 text-sm text-ink-muted hover:bg-ink/[0.04] [&::-webkit-details-marker]:hidden">
        <CaretDown size={14} weight="bold" className="-rotate-90 transition-transform group-open:rotate-0" aria-hidden />
        {count === 1 ? "Keeping 1 thing you asked for" : `Keeping ${count} things you asked for`}
      </summary>
      <ul className="mt-1 flex flex-col">
        {review.wishes.map((wish) => (
          <li key={wishKey(wish)} className="flex items-center justify-between gap-2 pl-8 text-sm">
            <span>{wish.text || "A piece you asked us to keep"}</span>
            <Button variant="quiet" aria-disabled={review.looking} aria-label={`Stop keeping: ${wish.text}`}
              onClick={() => review.keep(withoutWish(review.wishes, wish), findingId, onRelook)}>
              Remove
            </Button>
          </li>
        ))}
      </ul>
    </details>
  );
}

export function ReviewFailure({ review }: { review: ProposalReviewState }) {
  if (!review.failed) return null;
  return <p role="alert" className="text-problem">We couldn&apos;t reach the layout search just now. Check your connection and try again.</p>;
}
