"use client";

import { CircleNotch, MagicWand } from "@phosphor-icons/react";
import { useId } from "react";
import { Button } from "@/components/ui/Button";
import { Tooltip } from "@/components/ui/Tooltip";
import { phaseSentence } from "@/lib/rearrangement-copy";
import type { NodeMove, RearrangementStatus } from "@/types/contracts";
import { type RearrangementSuggestion, useRearrangementSuggestion } from "./useRearrangementSuggestion";

const LABEL = "Suggest a rearrangement";

type Props = { scanId: string; revision: number; hasMoves: boolean; onSuggested: (moves: NodeMove[]) => void };

function Working({ phase }: { phase: RearrangementStatus["phase"] }) {
  return (
    <p className="flex items-start gap-2 font-medium" role="status">
      <CircleNotch size={18} className="mt-0.5 shrink-0 animate-spin text-ink-muted motion-reduce:animate-none" aria-hidden />
      {phaseSentence(phase)}
    </p>
  );
}

function Unavailable({ reason }: { reason: string }) {
  const reasonId = useId();
  return (
    <Button
      variant="chip"
      aria-label={LABEL}
      aria-disabled
      aria-describedby={reasonId}
      className="group/icon relative cursor-not-allowed"
    >
      <span className="flex items-center gap-2 opacity-40">
        <MagicWand size={16} weight="bold" aria-hidden />
        {LABEL}
      </span>
      <Tooltip id={reasonId} label={reason} side="above" />
    </Button>
  );
}

function Ask({ onAsk, again }: { onAsk: () => void; again: boolean }) {
  return (
    <Button variant="chip" onClick={onAsk}>
      <MagicWand size={16} weight="bold" aria-hidden />
      {again ? "Suggest another rearrangement" : LABEL}
    </Button>
  );
}

function Note({ suggestion }: { suggestion: RearrangementSuggestion }) {
  const { answered, problem } = suggestion;
  if (problem) return <p className="text-problem" role="status">{problem}</p>;
  if (answered && !answered.accepted) return <p role="status">{answered.message}</p>;
  return null;
}

function Settled({ suggestion, hasMoves }: { suggestion: RearrangementSuggestion; hasMoves: boolean }) {
  const { answered } = suggestion;
  if (answered?.accepted && hasMoves) return <p className="font-medium" role="status">{answered.message}</p>;
  return (
    <div className="flex flex-col items-start gap-2">
      <Note suggestion={suggestion} />
      <Ask onAsk={suggestion.ask} again={answered !== null} />
    </div>
  );
}

/** Asks the fine-tuned model for a layout and loads the moves it gets back as the pending arrangement. */
export function SuggestRearrangement({ scanId, revision, hasMoves, onSuggested }: Props) {
  const suggestion = useRearrangementSuggestion(scanId, revision, onSuggested);
  const { status } = suggestion;
  if (status === null) return null;
  if (!status.available) return <Unavailable reason={status.unavailable_reason ?? ""} />;
  if (suggestion.working) return <Working phase={status.phase} />;
  return <Settled suggestion={suggestion} hasMoves={hasMoves} />;
}
