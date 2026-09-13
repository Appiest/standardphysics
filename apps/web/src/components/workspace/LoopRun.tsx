"use client";

import { ArrowsClockwise, CheckCircle, CircleNotch, MinusCircle } from "@phosphor-icons/react";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { runLoop } from "@/lib/layout-client";
import { deciderSentence, passOutcome, passTitle, summary } from "@/lib/loop-copy";
import type { LoopPass, LoopResult, NodeMove } from "@/types/contracts";

type Props = { scanId: string; revision: number; onTry: (moves: NodeMove[]) => void };

function PassMark({ kept }: { kept: boolean | null }) {
  if (kept) return <CheckCircle size={20} weight="fill" className="mt-0.5 shrink-0 text-pass" aria-label="Kept" />;
  return <MinusCircle size={20} className="mt-0.5 shrink-0 text-ink-faint" aria-label="Nothing kept" />;
}

function PassRow({ loopPass }: { loopPass: LoopPass }) {
  return (
    <li className="flex gap-3">
      <PassMark kept={loopPass.kept} />
      <div className="min-w-0">
        <p className="font-semibold">
          {loopPass.number}. {passTitle(loopPass)}
        </p>
        <p className="mt-0.5 text-ink-muted">{passOutcome(loopPass)}</p>
      </div>
    </li>
  );
}

function Result({ result, onTry }: { result: LoopResult; onTry: Props["onTry"] }) {
  return (
    <div className="rounded-lg bg-sheet p-4 shadow-[0_1px_2px_rgb(27_28_30/0.1),0_8px_24px_rgb(27_28_30/0.07)]" role="status">
      <ol className="flex flex-col gap-3">
        {result.passes.map((loopPass) => <PassRow key={loopPass.number} loopPass={loopPass} />)}
      </ol>
      <p className="mt-4 font-medium">{summary(result)}</p>
      <p className="mt-1 text-sm text-ink-faint">{deciderSentence(result)}</p>
      {result.moves.length > 0 && (
        <Button variant="primary" className="mt-4" autoFocus onClick={() => onTry(result.moves)}>
          Try this layout
        </Button>
      )}
    </div>
  );
}

export function LoopRun({ scanId, revision, onTry }: Props) {
  const [state, setState] = useState<"idle" | "running" | "failed">("idle");
  const [result, setResult] = useState<LoopResult | null>(null);

  async function run() {
    if (state === "running") return;
    setState("running");
    try {
      setResult(await runLoop(scanId, revision));
      setState("idle");
    } catch {
      setState("failed");
    }
  }

  if (result) return <Result result={result} onTry={onTry} />;
  const running = state === "running";
  return (
    <div className="px-3">
      <Button variant="primary" onClick={run} aria-disabled={running}>
        {running ? <CircleNotch size={18} className="animate-spin" aria-hidden /> : <ArrowsClockwise size={18} weight="bold" aria-hidden />}
        {running ? "Working through the findings" : "Fix what I can"}
      </Button>
      <p className="mt-2 text-sm text-ink-muted" role="status">
        {running
          ? "Each pass moves furniture, measures the shop again, and keeps the change only if it helps. This takes about half a minute."
          : "Moves your furniture one step at a time, re-measuring after each step."}
      </p>
      {state === "failed" && <p className="mt-2 text-problem">We couldn&apos;t run that just now. Try again.</p>}
    </div>
  );
}
