"use client";

import { ArrowsClockwise, CheckCircle, CircleNotch, MinusCircle, Stop } from "@phosphor-icons/react";
import { useEffect, useReducer, useRef } from "react";
import { Button } from "@/components/ui/Button";
import { ApiRefusal, streamLoop } from "@/lib/layout-client";
import { announcement, deciderSentence, passOutcome, passTitle, stoppedSentence, summary, workingSentence } from "@/lib/loop-copy";
import { advanceLoop, NOT_STARTED, type LoopProgress } from "@/lib/loop-progress";
import type { LoopPass, LoopResult, NodeMove } from "@/types/contracts";

type Props = { scanId: string; revision: number; onTry: (moves: NodeMove[]) => void };

const UNABLE_TO_START = "Unable to start the loop. Check that the API is running, then try again.";

function useLoopStream(scanId: string, revision: number, onTry: Props["onTry"]) {
  const [progress, dispatch] = useReducer(advanceLoop, NOT_STARTED);
  const connection = useRef<AbortController | null>(null);

  useEffect(() => () => connection.current?.abort(), []);

  async function start() {
    const controller = new AbortController();
    connection.current = controller;
    dispatch({ kind: "start" });
    try {
      await streamLoop(scanId, revision, (event) => {
        dispatch(event);
        if (!controller.signal.aborted && event.kind === "finished" && event.result.base_revision === revision && event.result.moves.length > 0) onTry(event.result.moves);
      }, controller.signal);
      dispatch({ kind: "closed" });
    } catch (reason) {
      if (controller.signal.aborted) return;
      dispatch({ kind: "refused", error: reason instanceof ApiRefusal && reason.error ? reason.error : UNABLE_TO_START });
    }
  }

  function stop() {
    dispatch({ kind: "stop" });
    connection.current?.abort();
  }

  return { progress, start, stop };
}

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

function WorkingRow({ progress }: { progress: LoopProgress }) {
  return (
    <li className="flex gap-3">
      <CircleNotch size={20} className="mt-0.5 shrink-0 text-ink-faint motion-safe:animate-spin" aria-hidden />
      <div className="min-w-0">
        <p className="font-semibold">{progress.passes.length + 1}. Working on this pass</p>
        <p className="mt-0.5 text-ink-muted">{workingSentence(progress.decidedBy)}</p>
      </div>
    </li>
  );
}

function Finished({ result, onTry }: { result: LoopResult; onTry: Props["onTry"] }) {
  return (
    <>
      <p className="mt-4 font-medium">{summary(result)}</p>
      <p className="mt-1 text-sm text-ink-faint">{deciderSentence(result.decided_by)}</p>
      {result.moves.length > 0 && (
        <Button variant="primary" className="mt-4" autoFocus onClick={() => onTry(result.moves)}>
          Try this layout
        </Button>
      )}
    </>
  );
}

function StartButton({ onStart, again = false }: { onStart: () => void; again?: boolean }) {
  return (
    <Button variant="primary" onClick={onStart}>
      <ArrowsClockwise size={18} weight="bold" aria-hidden />
      {again ? "Start the loop again" : "Start improvement loop"}
    </Button>
  );
}

function Outcome({ progress, onTry, onStart, onStop }: { progress: LoopProgress; onTry: Props["onTry"]; onStart: () => void; onStop: () => void }) {
  if (progress.phase === "running") {
    return (
      <Button variant="quiet" className="mt-3 -ml-3" onClick={onStop}>
        <Stop size={16} weight="bold" aria-hidden />
        Stop the loop
      </Button>
    );
  }
  if (progress.result) return <Finished result={progress.result} onTry={onTry} />;
  const stopped = progress.phase === "stopped";
  return (
    <div className="mt-4 flex flex-col items-start gap-3">
      <p className={stopped ? "text-ink-muted" : "text-problem"}>{stopped ? stoppedSentence(progress.passes.length) : progress.error}</p>
      <StartButton onStart={onStart} again />
    </div>
  );
}

export function LoopRun({ scanId, revision, onTry }: Props) {
  const { progress, start, stop } = useLoopStream(scanId, revision, onTry);
  return (
    <>
      <p className="sr-only" role="status">{announcement(progress)}</p>
      {progress.phase === "idle" ? (
        <div className="px-3">
          <StartButton onStart={start} />
          <p className="mt-2 text-sm text-ink-muted">Moves your furniture one step at a time and re-measures after each step. You&apos;ll see every step here as it happens.</p>
        </div>
      ) : (
        <div className="rounded-lg bg-sheet p-4 shadow-[0_1px_2px_rgb(27_28_30/0.1),0_8px_24px_rgb(27_28_30/0.07)]">
          <ol className="flex flex-col gap-3" aria-label="Improvement loop passes">
            {progress.passes.map((loopPass) => <PassRow key={loopPass.number} loopPass={loopPass} />)}
            {progress.phase === "running" && <WorkingRow progress={progress} />}
          </ol>
          <Outcome progress={progress} onTry={onTry} onStart={start} onStop={stop} />
        </div>
      )}
    </>
  );
}
