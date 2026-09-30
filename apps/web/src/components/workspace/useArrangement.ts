"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type Checked, LayoutChecker, layoutKey } from "@/lib/layout-checker";
import { type ArrangementEvent, type MovesSource, sourceAfter } from "@/lib/arrangement-source";
import { ApiRefusal, checkLayout, putBackSuggestion, saveLayout } from "@/lib/layout-client";
import { applyMoves, type MoveSet, withMove } from "@/lib/moves";
import type { Blocked, LayoutCheckResult, NodeMove, SceneGraph } from "@/types/contracts";

/** How long a piece rests under the pointer before the layout is checked mid-drag. */
const DRAG_SETTLE_MS = 160;
const NUDGE_SETTLE_MS = 350;
const SCANNED_LAYOUT = layoutKey({});
const NO_BLOCKS: Blocked[] = [];

export type Arrangement = ReturnType<typeof useArrangement>;

/** Where a finished layout goes. The workspace saves it as the shop's record; the owner view saves it as a plan. */
export type Persist = (scanId: string, baseRevision: number, moves: NodeMove[], suggestionId?: string) => Promise<unknown>;

/**
 * What the screen shows once a layout is saved. The shop's record comes back as
 * a new revision with the moves already in it, so they are cleared; a plan
 * leaves the shop as scanned, so its moves stay where the owner left them.
 */
export type AfterSave = "clear" | "keep";

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
  const seed = useCallback((moves: MoveSet, result: LayoutCheckResult) => checker().seed(moves, result), [checker]);

  return { cached, seed, checking, latencyMs, request, cancel };
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

/**
 * Which suggestion the pending moves came from, if any, and taking one back. A
 * suggestion the model made is recorded on the server, so leaving it unsaved
 * tells the server it was put back rather than forgetting it silently.
 */
function useSuggestion(scanId: string, revision: number, setProblem: (problem: string) => void) {
  const [source, setSource] = useState<MovesSource>(null);
  const [puttingBack, setPuttingBack] = useState(false);
  const suggestionId = useRef<string | null>(null);
  const puttingBackRef = useRef(false);

  const mark = useCallback((event: ArrangementEvent, id: string | null = null) => {
    if (event !== "suggested" || id) suggestionId.current = id;
    setSource(sourceAfter(event));
  }, []);

  /** Tells the server the loaded suggestion was put back; false when it couldn't be reached. */
  const release = useCallback(async () => {
    const id = suggestionId.current;
    if (!id) return true;
    if (puttingBackRef.current) return false;
    puttingBackRef.current = true;
    setPuttingBack(true);
    try {
      await putBackSuggestion(scanId, revision, id);
      if (suggestionId.current === id) suggestionId.current = null;
      return true;
    } catch {
      setProblem("Couldn't put the suggested layout back. Check your connection and try again.");
      return false;
    } finally {
      puttingBackRef.current = false;
      setPuttingBack(false);
    }
  }, [scanId, revision, setProblem]);

  return { source, puttingBack, suggestionId, mark, release };
}

function useSave(scanId: string, revision: number, persist: Persist, afterSave: AfterSave, movesRef: { current: MoveSet }, suggestionId: { current: string | null }, reset: () => void, setProblem: (problem: string) => void) {
  const router = useRouter();
  const [saving, setSaving] = useState(false);
  const [savedKey, setSavedKey] = useState<string | null>(null);
  const save = useCallback(async () => {
    setSaving(true);
    try {
      await persist(scanId, revision, Object.values(movesRef.current), suggestionId.current ?? undefined);
      setSavedKey(layoutKey(movesRef.current));
      if (afterSave === "clear") reset();
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
  }, [scanId, revision, persist, movesRef, suggestionId, afterSave, reset, router, setProblem]);
  return { save, saving, savedKey, setSavedKey };
}

function movesOf(proposed: NodeMove[]): MoveSet {
  return Object.fromEntries(proposed.map((move) => [move.node_id, move]));
}

export function useArrangement(scanId: string, scene: SceneGraph, persist: Persist = saveLayout, afterSave: AfterSave = "clear") {
  const state = useLayoutState();
  const { layout, setLayout, movesRef, legalRef, place } = state;
  const { cached: cachedCheck, seed, checking, latencyMs, request, cancel } = useChecker(scanId, scene.revision, state);
  const settle = useSettleTimer();
  const [activeId, setActiveId] = useState<string | null>(null);
  const setProblem = useCallback((problem: string) => setLayout((current) => ({ ...current, problem })), [setLayout]);
  const suggestion = useSuggestion(scanId, scene.revision, setProblem);
  const { mark } = suggestion;

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
    mark("moved");
    settle.after(DRAG_SETTLE_MS, () => request(movesRef.current, false));
  }, [place, movesRef, mark, settle, request]);

  /** Slides or turns a piece a step: the one in hand, or the one named, which a keyboard can do before the pick has rendered. */
  const nudge = useCallback((dx: number, dy: number, degrees: number, nodeId: string | null = activeId) => {
    if (!nodeId) return;
    place(withMove(movesRef.current, nodeId, dx, dy, degrees), { refused: NO_BLOCKS });
    mark("moved");
    settle.after(NUDGE_SETTLE_MS, drop);
  }, [activeId, place, movesRef, mark, settle, drop]);

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
    mark("cleared");
  }, [movesRef, legalRef, layout.history, jumpTo, mark]);

  /** Puts a whole layout on the plan. A check still on its way describes the layout before it, so it is dropped. */
  const loadFrom = useCallback((proposed: NodeMove[], event: ArrangementEvent, id: string | null = null, known?: LayoutCheckResult | null) => {
    const moves = movesOf(proposed);
    cancel();
    settle.clear();
    if (known) seed(moves, known);
    place(moves, { refused: NO_BLOCKS, problem: null });
    mark(event, id);
    setActiveId(proposed[0]?.node_id ?? null);
    request(moves, true);
  }, [cancel, settle, seed, place, mark, request]);
  /** Loads a layout; `known` is its check when one came with it, such as the check a Fix room run ends with. */
  const load = useCallback((proposed: NodeMove[], known?: LayoutCheckResult | null) => loadFrom(proposed, "loaded", null, known), [loadFrom]);
  /** Loads the model's suggested layout, remembered by id so a save or a put-back reaches the server. */
  const loadSuggestion = useCallback((proposed: NodeMove[], id: string) => loadFrom(proposed, "suggested", id), [loadFrom]);

  const preview = useCallback((proposed: NodeMove[]) => {
    cancel();
    place(movesOf(proposed), { check: null, problem: null, refused: NO_BLOCKS });
    mark("loaded");
  }, [cancel, place, mark]);

  /** Forgets every move and every undo step, for leaving or after a save. */
  const clearPending = useCallback(() => {
    cancel();
    settle.clear();
    legalRef.current = {};
    movesRef.current = {};
    setLayout((current) => ({ ...EMPTY_LAYOUT, baseline: current.baseline }));
    setActiveId(null);
    mark("cleared");
  }, [cancel, settle, legalRef, movesRef, setLayout, mark]);

  /** Puts a loaded suggestion back on the server, then forgets every move; false when the server couldn't be told. */
  const { release, suggestionId } = suggestion;
  const reset = useCallback(async () => {
    if (suggestionId.current && !(await release())) return false;
    clearPending();
    return true;
  }, [suggestionId, release, clearPending]);

  const { save, saving, savedKey, setSavedKey } = useSave(scanId, scene.revision, persist, afterSave, movesRef, suggestionId, clearPending, setProblem);

  /** Brings back a layout saved earlier, unless the owner has already started moving pieces. */
  const restore = useCallback((saved: NodeMove[]) => {
    if (Object.keys(movesRef.current).length > 0 || saved.length === 0) return;
    loadFrom(saved, "loaded");
    setActiveId(null);
    setSavedKey(layoutKey(movesOf(saved)));
  }, [movesRef, loadFrom, setSavedKey]);

  const { moves, check } = layout;
  const hasMoves = Object.keys(moves).length > 0;
  const saved = savedKey === layoutKey(moves);
  const blockedIds = useMemo(() => new Set(check?.blocked.map((b) => b.node_id) ?? []), [check]);
  const canSave = hasMoves && !saved && !checking && !saving && check !== null && check.blocked.length === 0;

  return {
    shown, moves, check, checking, saving, saved, problem: layout.problem, activeId, hasMoves, blockedIds, canSave,
    baseline: layout.baseline, refused: layout.refused, canUndo: layout.history.length > 0, latencyMs,
    source: suggestion.source, puttingBack: suggestion.puttingBack,
    setActiveId, drag, drop, nudge, reset, clearPending, putBack, undo, start, save, load, loadSuggestion, preview, restore,
  };
}
