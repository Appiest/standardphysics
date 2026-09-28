"use client";

import { ArrowRight, ChatCircleText, CircleNotch } from "@phosphor-icons/react";
import { useState, type FormEvent } from "react";
import { ActivitySpinner } from "@/components/ui/ActivitySpinner";
import { Button } from "@/components/ui/Button";
import { askAboutShop } from "@/lib/layout-client";
import type { AskAnswer, Locus, NodeMove } from "@/types/contracts";

type AskBoxProps = {
  scanId: string;
  revision: number;
  onLook: (locus: Locus | null) => void;
  onTry: (moves: NodeMove[]) => void;
};

function AnswerCard({ answer, onTry }: { answer: AskAnswer; onTry: AskBoxProps["onTry"] }) {
  const proposal = answer.proposal;
  return (
    <div className="mt-3 rounded-lg bg-sheet p-3 shadow-[0_1px_2px_rgb(27_28_30/0.08)]" role="status">
      <p className={answer.understood ? "font-medium" : "text-ink-muted"}>{answer.text}</p>
      {proposal && (
        <Button variant="primary" className="mt-3" onClick={() => onTry(proposal.moves)}>
          Try this layout
        </Button>
      )}
    </div>
  );
}

export function AskBox({ scanId, revision, onLook, onTry }: AskBoxProps) {
  const [text, setText] = useState("");
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<AskAnswer | null>(null);
  const [failed, setFailed] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!text.trim()) return;
    setAsking(true);
    setFailed(false);
    try {
      const reply = await askAboutShop(scanId, revision, text.trim());
      setAnswer(reply);
      onLook(reply.locus);
    } catch {
      setFailed(true);
    } finally {
      setAsking(false);
    }
  }

  return (
    <section className="mb-6 px-3">
      <form onSubmit={submit} className="flex gap-2">
        <label className="sr-only" htmlFor="ask-box">Ask about your shop</label>
        <div className="relative flex-1">
          <ChatCircleText size={18} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint" aria-hidden />
          <input
            id="ask-box"
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="How many chairs do I have?"
            className="w-full rounded-lg border border-rule bg-sheet py-2.5 pl-9 pr-3 text-base placeholder:text-ink-faint focus-visible:outline-accent"
          />
        </div>
        <Button variant="primary" type="submit" disabled={asking} aria-label="Ask" className="px-3.5 py-2.5 disabled:opacity-60">
          {asking ? <CircleNotch size={18} className="animate-spin" aria-hidden /> : <ArrowRight size={18} weight="bold" aria-hidden />}
        </Button>
      </form>
      {failed && <ActivitySpinner className="mt-2" />}
      {answer && <AnswerCard answer={answer} onTry={onTry} />}
    </section>
  );
}
