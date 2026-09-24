"use client";

import { useRouter } from "next/navigation";
import { useCallback, useMemo, useRef, useState } from "react";
import { ApiRefusal, checkLayout, putBackSuggestion, saveLayout } from "@/lib/layout-client";
import { type ArrangementEvent, type MovesSource, sourceAfter } from "@/lib/arrangement-source";
import { applyMoves, type MoveSet, withMove } from "@/lib/moves";
import type { LayoutCheckResult, NodeMove, SceneGraph } from "@/types/contracts";

const NUDGE_SETTLE_MS = 350;

export type Arrangement = ReturnType<typeof useArrangement>;

export function useArrangement(scanId: string, scene: SceneGraph) {
  const router = useRouter();
  const [moves, setMoves] = useState<MoveSet>({});
  const [check, setCheck] = useState<LayoutCheckResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [puttingBack, setPuttingBack] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [source, setSource] = useState<MovesSource>(null);
  const latestSequence = useRef(0);
  const settleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const movesRef = useRef<MoveSet>({});
  const suggestionId = useRef<string | null>(null);
  const puttingBackRef = useRef(false);

  const shown = useMemo(() => applyMoves(scene, moves), [scene, moves]);

  const runCheck = useCallback(async () => {
    const sequence = ++latestSequence.current;
    setChecking(true);
    try {
      const result = await checkLayout(scanId, scene.revision, sequence, Object.values(movesRef.current));
      if (result.sequence !== latestSequence.current) return;
      setCheck(result);
      setProblem(null);
    } catch {
      if (sequence === latestSequence.current) setProblem("We couldn't check that layout. Try moving it again.");
    } finally {
      if (sequence === latestSequence.current) setChecking(false);
    }
  }, [scanId, scene.revision]);

  const update = useCallback((next: MoveSet, event: ArrangementEvent) => {
    movesRef.current = next;
    setMoves(next);
    setSource(sourceAfter(event));
  }, []);

  const drag = useCallback(
    (nodeId: string, dx: number, dy: number) => update(withMove(movesRef.current, nodeId, dx, dy, 0), "moved"),
    [update],
  );

  const settle = useCallback(() => {
    if (settleTimer.current) clearTimeout(settleTimer.current);
    settleTimer.current = setTimeout(runCheck, NUDGE_SETTLE_MS);
  }, [runCheck]);

  const nudge = useCallback(
    (dx: number, dy: number, degrees: number) => {
      if (!activeId) return;
      update(withMove(movesRef.current, activeId, dx, dy, degrees), "moved");
      settle();
    },
    [activeId, update, settle],
  );

  const loadFrom = useCallback(
    (proposed: NodeMove[], event: ArrangementEvent) => {
      if (event !== "suggested") suggestionId.current = null;
      update(Object.fromEntries(proposed.map((move) => [move.node_id, move])), event);
      setActiveId(proposed[0]?.node_id ?? null);
      runCheck();
    },
    [update, runCheck],
  );
  const load = useCallback((proposed: NodeMove[]) => loadFrom(proposed, "loaded"), [loadFrom]);
  const loadSuggestion = useCallback((proposed: NodeMove[], id: string) => {
    suggestionId.current = id;
    loadFrom(proposed, "suggested");
  }, [loadFrom]);

  const preview = useCallback((proposed: NodeMove[]) => {
    latestSequence.current += 1;
    suggestionId.current = null;
    update(Object.fromEntries(proposed.map((move) => [move.node_id, move])), "loaded");
    setCheck(null);
    setChecking(false);
    setProblem(null);
  }, [update]);

  const clearPending = useCallback(() => {
    latestSequence.current += 1;
    update({}, "cleared");
    setCheck(null);
    setChecking(false);
    setProblem(null);
    setActiveId(null);
  }, [update]);

  const reset = useCallback(async () => {
    if (puttingBackRef.current) return false;
    const id = suggestionId.current;
    if (id) {
      puttingBackRef.current = true;
      setPuttingBack(true);
      try {
        await putBackSuggestion(scanId, scene.revision, id);
      } catch {
        setProblem("Couldn't put the suggested layout back. Check your connection and try again.");
        puttingBackRef.current = false;
        setPuttingBack(false);
        return false;
      }
      puttingBackRef.current = false;
      setPuttingBack(false);
      if (suggestionId.current !== id) return true;
      suggestionId.current = null;
    }
    clearPending();
    return true;
  }, [scanId, scene.revision, clearPending]);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      await saveLayout(scanId, scene.revision, Object.values(movesRef.current), suggestionId.current ?? undefined);
      suggestionId.current = null;
      clearPending();
      router.refresh();
      setTimeout(() => router.refresh(), 3000);
      return true;
    } catch (error) {
      const stale = error instanceof ApiRefusal && error.status === 409 && error.error.startsWith("a newer layout");
      if (stale) {
        suggestionId.current = null;
        clearPending();
        router.refresh();
        setProblem("Someone saved a newer layout, so we loaded it. Make your moves again on this one.");
      } else {
        setProblem("That layout couldn't be saved. Check the pieces marked in red.");
      }
      return false;
    } finally {
      setSaving(false);
    }
  }, [scanId, scene.revision, clearPending, router]);

  const hasMoves = Object.keys(moves).length > 0;
  const blockedIds = useMemo(() => new Set(check?.blocked.map((b) => b.node_id) ?? []), [check]);
  const canSave = hasMoves && !checking && !saving && !puttingBack && check !== null && check.blocked.length === 0;

  return {
    shown, moves, source, check, checking, saving, puttingBack, problem, activeId, hasMoves, blockedIds, canSave,
    setActiveId, drag, drop: runCheck, nudge, reset, save, load, loadSuggestion, preview,
  };
}
