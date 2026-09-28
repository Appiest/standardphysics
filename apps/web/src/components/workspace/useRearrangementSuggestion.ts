"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { rearrangementStatus, startRearrangement } from "@/lib/layout-client";
import { isWorking, refusalSentence } from "@/lib/rearrangement-copy";
import type { NodeMove, RearrangementStatus } from "@/types/contracts";

const POLL_MS = 2000;

export type RearrangementSuggestion = ReturnType<typeof useRearrangementSuggestion>;

function useStatusOnOpen(scanId: string, revision: number, setStatus: (status: RearrangementStatus) => void, setAsked: (asked: boolean) => void) {
  useEffect(() => {
    let live = true;
    rearrangementStatus(scanId, revision)
      .then((status) => {
        if (!live) return;
        setStatus(status);
        if (isWorking(status)) setAsked(true);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [scanId, revision, setStatus, setAsked]);
}

function usePolling(scanId: string, status: RearrangementStatus | null, setStatus: (next: RearrangementStatus) => void, setTrouble: (text: string) => void) {
  useEffect(() => {
    if (!status || !isWorking(status)) return;
    const timer = setTimeout(() => {
      rearrangementStatus(scanId, status.base_revision).then(setStatus).catch((error) => setTrouble(refusalSentence(error)));
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [scanId, status, setStatus, setTrouble]);
}

function useDeliverMoves(status: RearrangementStatus | null, asked: boolean, onMoves: (moves: NodeMove[], suggestionId: string) => void) {
  const delivered = useRef<string | null>(null);
  useEffect(() => {
    const result = status?.state === "done" ? status.result : null;
    if (!asked || !result?.accepted || !result.suggestion_id || delivered.current === result.graph_hash) return;
    delivered.current = result.graph_hash;
    onMoves(result.moves, result.suggestion_id);
  }, [status, asked, onMoves]);
  return useCallback(() => {
    delivered.current = null;
  }, []);
}

function outcome(status: RearrangementStatus | null, asked: boolean, trouble: string | null) {
  const settled = asked ? status : null;
  return {
    working: isWorking(status) && trouble === null,
    answered: settled?.state === "done" ? settled.result : null,
    problem: trouble ?? (settled?.state === "failed" ? settled.error : null),
  };
}

/** One suggestion from the fine-tuned model for this revision: start it, poll it, hand accepted moves over once. */
export function useRearrangementSuggestion(scanId: string, revision: number, onMoves: (moves: NodeMove[], suggestionId: string) => void) {
  const [status, setStatus] = useState<RearrangementStatus | null>(null);
  const [asked, setAsked] = useState(false);
  const [trouble, setTrouble] = useState<string | null>(null);

  useStatusOnOpen(scanId, revision, setStatus, setAsked);
  usePolling(scanId, status, setStatus, setTrouble);
  const forgetDelivered = useDeliverMoves(status, asked, onMoves);

  const ask = useCallback(async () => {
    setTrouble(null);
    forgetDelivered();
    try {
      const started = await startRearrangement(scanId, revision);
      // Asked flips only with the new job's status: flipping it first would deliver the last job's moves.
      setStatus(started);
      setAsked(true);
    } catch (error) {
      setTrouble(refusalSentence(error));
    }
  }, [scanId, revision, forgetDelivered]);

  return { status, ...outcome(status, asked, trouble), ask };
}
