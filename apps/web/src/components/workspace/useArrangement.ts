"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type Checked, LayoutChecker, layoutKey } from "@/lib/layout-checker";
import { ApiRefusal, checkLayout, saveLayout } from "@/lib/layout-client";
import { applyMoves, type MoveSet, withMove } from "@/lib/moves";
import type { Blocked, LayoutCheckResult, NodeMove, SceneGraph } from "@/types/contracts";

/** How long a piece rests under the pointer before the layout is checked mid-drag. */
const DRAG_SETTLE_MS = 160;
const NUDGE_SETTLE_MS = 350;
const SCANNED_LAYOUT = layoutKey({});
const NO_BLOCKS: Blocked[] = [];

export type Arrangement = ReturnType<typeof useArrangement>;

/** Where a finished layout goes. The workspace saves it as the shop's record; the owner view saves it as a plan. */
export type Persist = (scanId: string, baseRevision: number, moves: NodeMove[]) => Promise<unknown>;

type Layout = {
  moves: MoveSet;
  check: LayoutCheckResult | null;
  baseline: LayoutCheckResult | null;
  refused: Blocked[];
  history: MoveSet[];
  problem: string | null;
};

const EMPTY_LAYOUT: Layout = { moves: {}, check: null, baseline: null, refused: NO_BLOCKS, history: [], problem: null };

/** What the screen shows for a checked layout: nothing extra for the scanned one, whose answer is the baseline. */
function shownCheck(checked: Checked): LayoutCheckResult | null {
  return checked.key === SCANNED_LAYOUT ? null : checked.result;
}

/**
 * The pieces being moved, the check of where they are now, and the last layout
 * that broke no hard constraint. A drop that breaks one snaps back to that
 * layout and says why; any other drop becomes a step undo can return to.
 */
function useLayoutState() {
  const [layout, setLayout] = useState<Layout>(EMPTY_LAYOUT);
  const movesRef = useRef<MoveSet>({});
  const legalRef = useRef<MoveSet>({});

  const place = useCallback((moves: MoveSet, extra: Partial<Layout> = {}) => {
    movesRef.current = moves;
    setLayout((current) => ({ ...current, ...extra, moves }));
  }, []);

  const showResult = useCallback((checked: Checked) => {
    setLayout((current) => {
      const baseline = checked.key === SCANNED_LAYOUT ? checked.result : current.baseline;
      const stillShown = layoutKey(movesRef.current) === checked.key || layoutKey(movesRef.current) !== SCANNED_LAYOUT;
      return { ...current, baseline, check: stillShown ? shownCheck(checked) : current.check, problem: null };
    });
  }, []);

  const commit = useCallback((checked: Checked, cached: (moves: MoveSet) => LayoutCheckResult | undefined) => {
    const legal = legalRef.current;
    if (checked.result.blocked.length > 0) {
      place(legal, { refused: checked.result.blocked, check: layoutKey(legal) === SCANNED_LAYOUT ? null : cached(legal) ?? null });
      return;
    }
    legalRef.current = checked.moves;
    if (layoutKey(legal) === checked.key) return;
    setLayout((current) => ({ ...current, refused: NO_BLOCKS, history: [...current.history, legal] }));
  }, [place]);

  return { layout, setLayout, movesRef, legalRef, place, showResult, commit };
}

type LayoutState = ReturnType<typeof useLayoutState>;

/**
 * The checker for this scan and revision, made on first use and replaced when
 * the revision changes, since answers cached against one revision say nothing
 * about the next.
 */
function useChecker(scanId: string, revision: number, state: LayoutState) {
  const [checking, setChecking] = useState(false);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);
  const commitKey = useRef<string | null>(null);
  const made = useRef<{ key: string; checker: LayoutChecker } | null>(null);
  const { showResult, commit, setLayout } = state;

  const checker = useCallback((): LayoutChecker => {
    const key = `${scanId}@${revision}`;
    if (made.current?.key === key) return made.current.checker;
    made.current?.checker.cancel();
    const fresh = new LayoutChecker((moves, sequence) => checkLayout(scanId, revision, sequence, moves), {
      onBusy: setChecking,
      onResult: (checked) => {
        if (checked.milliseconds > 0) setLatencyMs(Math.round(checked.milliseconds));
        showResult(checked);
        if (commitKey.current !== checked.key) return;
        commitKey.current = null;
        commit(checked, (moves) => fresh.cached(moves));
      },
      onError: (moves) => {
        if (commitKey.current === layoutKey(moves)) commitKey.current = null;
        setLayout((current) => ({ ...current, problem: "We couldn't check that layout. Try moving it again." }));
      },
    });
    made.current = { key, checker: fresh };
    return fresh;
  }, [scanId, revision, showResult, commit, setLayout]);

  const request = useCallback((moves: MoveSet, commitIt: boolean) => {
    if (commitIt) commitKey.current = layoutKey(moves);
    checker().request(moves);
  }, [checker]);

  const cancel = useCallback(() => {
    commitKey.current = null;
    checker().cancel();
  }, [checker]);

  const cached = useCallback((moves: MoveSet) => checker().cached(moves), [checker]);

  return { cached, checking, latencyMs, request, cancel };
}

/** One pending check at a time: a new drag or nudge replaces the one waiting. */
function useSettleTimer() {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clear = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);
  const after = useCallback((milliseconds: number, run: () => void) => {
    clear();
    timer.current = setTimeout(run, milliseconds);
  }, [clear]);
  useEffect(() => clear, [clear]);
  return { after, clear };
}

function useSave(scanId: string, revision: number, persist: Persist, movesRef: { current: MoveSet }, reset: () => void, setProblem: (problem: string) => void) {
  const router = useRouter();
  const [saving, setSaving] = useState(false);
  const save = useCallback(async () => {
    setSaving(true);
    try {
      await persist(scanId, revision, Object.values(movesRef.current));
      reset();
      router.refresh();
      setTimeout(() => router.refresh(), 3000);
      return true;
    } catch (error) {
      const stale = error instanceof ApiRefusal && error.status === 409 && error.error.startsWith("a newer layout");
      if (stale) {
        reset();
        router.refresh();
        setProblem("Someone saved a newer layout, so we loaded it. Make your moves again on this one.");
      } else {
        setProblem("That layout couldn't be saved. Check the pieces marked in red.");
      }
      return false;
    } finally {
      setSaving(false);
    }
  }, [scanId, revision, persist, movesRef, reset, router, setProblem]);
  return { save, saving };
}

export function useArrangement(scanId: string, scene: SceneGraph, persist: Persist = saveLayout) {
  const state = useLayoutState();
  const { layout, setLayout, movesRef, legalRef, place } = state;
  const { cached: cachedCheck, checking, latencyMs, request, cancel } = useChecker(scanId, scene.revision, state);
  const settle = useSettleTimer();
  const [activeId, setActiveId] = useState<string | null>(null);

  const shown = useMemo(() => applyMoves(scene, layout.moves), [scene, layout.moves]);

  /** Checks the scanned layout once, so every later layout has something to be compared with. */
  const start = useCallback(() => {
    if (!cachedCheck({})) request({}, false);
  }, [cachedCheck, request]);

  const drop = useCallback(() => {
    settle.clear();
    request(movesRef.current, true);
  }, [settle, request, movesRef]);

  const drag = useCallback((nodeId: string, dx: number, dy: number) => {
    place(withMove(movesRef.current, nodeId, dx, dy, 0), { refused: NO_BLOCKS });
    settle.after(DRAG_SETTLE_MS, () => request(movesRef.current, false));
  }, [place, movesRef, settle, request]);

  /** Slides or turns a piece a step: the one in hand, or the one named, which a keyboard can do before the pick has rendered. */
  const nudge = useCallback((dx: number, dy: number, degrees: number, nodeId: string | null = activeId) => {
    if (!nodeId) return;
    place(withMove(movesRef.current, nodeId, dx, dy, degrees), { refused: NO_BLOCKS });
    settle.after(NUDGE_SETTLE_MS, drop);
  }, [activeId, place, movesRef, settle, drop]);

  /** Jumps straight to a layout already known to be legal, such as an undo step, using its cached check when there is one. */
  const jumpTo = useCallback((moves: MoveSet, history: MoveSet[]) => {
    cancel();
    settle.clear();
    legalRef.current = moves;
    const cached = cachedCheck(moves);
    const check = layoutKey(moves) === SCANNED_LAYOUT ? null : cached ?? null;
    place(moves, { history, check, refused: NO_BLOCKS, problem: null });
    if (!cached) request(moves, false);
  }, [cancel, settle, legalRef, cachedCheck, place, request]);

  const undo = useCallback(() => {
    const previous = layout.history.at(-1);
    if (previous) jumpTo(previous, layout.history.slice(0, -1));
  }, [layout.history, jumpTo]);

  /** Puts every piece back where the scan found it, as a step undo can take back. */
  const putBack = useCallback(() => {
    if (layoutKey(movesRef.current) === SCANNED_LAYOUT) return;
    jumpTo({}, [...layout.history, legalRef.current]);
    setActiveId(null);
  }, [movesRef, legalRef, layout.history, jumpTo]);

  const load = useCallback((proposed: NodeMove[]) => {
    const moves = Object.fromEntries(proposed.map((move) => [move.node_id, move]));
    place(moves, { refused: NO_BLOCKS });
    setActiveId(proposed[0]?.node_id ?? null);
    request(moves, true);
  }, [place, request]);

  const preview = useCallback((proposed: NodeMove[]) => {
    cancel();
    place(Object.fromEntries(proposed.map((move) => [move.node_id, move])), { check: null, problem: null, refused: NO_BLOCKS });
  }, [cancel, place]);

  /** Forgets every move and every undo step, for leaving or after a save. */
  const reset = useCallback(() => {
    cancel();
    settle.clear();
    legalRef.current = {};
    movesRef.current = {};
    setLayout((current) => ({ ...EMPTY_LAYOUT, baseline: current.baseline }));
    setActiveId(null);
  }, [cancel, settle, legalRef, movesRef, setLayout]);

  const setProblem = useCallback((problem: string) => setLayout((current) => ({ ...current, problem })), [setLayout]);
  const { save, saving } = useSave(scanId, scene.revision, persist, movesRef, reset, setProblem);

  const { moves, check } = layout;
  const hasMoves = Object.keys(moves).length > 0;
  const blockedIds = useMemo(() => new Set(check?.blocked.map((b) => b.node_id) ?? []), [check]);
  const canSave = hasMoves && !checking && !saving && check !== null && check.blocked.length === 0;

  return {
    shown, moves, check, checking, saving, problem: layout.problem, activeId, hasMoves, blockedIds, canSave,
    baseline: layout.baseline, refused: layout.refused, canUndo: layout.history.length > 0, latencyMs,
    setActiveId, drag, drop, nudge, reset, putBack, undo, start, save, load, preview,
  };
}
