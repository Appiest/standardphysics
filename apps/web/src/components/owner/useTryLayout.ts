"use client";

import { type KeyboardEvent, useCallback, useMemo, useState } from "react";
import type { Arrangement } from "@/components/workspace/useArrangement";
import { blockedSentence } from "@/lib/blocked-copy";
import { layoutChanges } from "@/lib/layout-changes";
import { nudgeForKey } from "@/lib/moves";
import type { Finding, SceneGraph } from "@/types/contracts";

export type LayoutView = "plan" | "model";

/**
 * What trying a layout needs beyond the arrangement itself: which view the
 * pieces are dragged on, what to say when a built-in piece is reached for, and
 * the keyboard's way of sliding a piece.
 */
export function useTryLayout(arrangement: Arrangement, scene: SceneGraph, scanned: Finding[]) {
  const [view, setView] = useState<LayoutView>("plan");
  const [fixedNote, setFixedNote] = useState<string | null>(null);
  const { setActiveId, nudge } = arrangement;

  const onFixedTap = useCallback((nodeId: string) => {
    const node = scene.nodes.find((candidate) => candidate.id === nodeId);
    if (!node) return;
    setFixedNote(blockedSentence({ node_id: nodeId, reason: "moved_something_fixed", detail: node.label }));
  }, [scene]);

  const onGrab = useCallback((nodeId: string) => {
    setFixedNote(null);
    setActiveId(nodeId);
  }, [setActiveId]);

  const onKey = useCallback((nodeId: string, event: KeyboardEvent, turnDegrees = 0) => {
    const handled = nudgeForKey(event, (dx, dy, degrees) => nudge(dx, dy, degrees, nodeId), turnDegrees);
    if (handled) onGrab(nodeId);
  }, [nudge, onGrab]);

  const before = arrangement.baseline?.findings ?? scanned;
  const { check } = arrangement;
  const drawn = useMemo(() => {
    const now = check?.findings ?? before;
    const cleared = check ? layoutChanges(before, check.findings).filter((change) => change.kind === "cleared").flatMap((change) => change.before ?? []) : [];
    return { problems: now.filter((finding) => finding.outcome === "problem"), cleared };
  }, [before, check]);

  const movedIds = useMemo(() => new Set(Object.keys(arrangement.moves)), [arrangement.moves]);

  return { view, setView, fixedNote, clearFixedNote: () => setFixedNote(null), onFixedTap, onGrab, onKey, drawn, movedIds };
}

export type TryLayout = ReturnType<typeof useTryLayout>;
