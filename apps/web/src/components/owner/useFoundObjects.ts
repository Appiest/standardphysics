"use client";

import { useCallback, useMemo, useState, useSyncExternalStore } from "react";
import type { FoundHandles } from "@/components/workspace/FoundOutlines";
import { type FoundGroup, foundGroups, foundMarks, rowCenter } from "@/lib/found-objects";
import type { SceneGraph, Vec3 } from "@/types/contracts";

/** Past this many pieces the camera stays on the whole shop; flying to the middle of 24 chairs shows none of them. */
const MOST_PIECES_TO_FLY_TO = 3;

/** The legend's footprint over the model: its `w-80` plus its `left-4` inset, in CSS pixels. */
export const LEGEND_INSET_PX = 320 + 16;
/** Tailwind's `lg`, where the legend sits over the model instead of the list sitting under the step. */
const WIDE_SCREEN = "(min-width: 64rem)";

export type FoundObjects = {
  groups: FoundGroup[];
  handles: FoundHandles;
  hoveredRowId: string | null;
  selectedRowId: string | null;
  /** Where the camera should look, when the owner picked a row it can frame. */
  focus: Vec3 | null;
  hoverRow: (rowId: string | null) => void;
  toggleRow: (rowId: string) => void;
  /** Selects the row a tapped piece belongs to, and says whether it belonged to one. */
  pickNode: (nodeId: string) => boolean;
  clear: () => void;
  legendOpen: boolean;
  setLegendOpen: (open: boolean) => void;
  /** How far right the model's picture moves, so the shop centres in the part the legend leaves uncovered. */
  frameShift: number;
};

function useFocus(scene: SceneGraph, groups: FoundGroup[], selectedRowId: string | null): Vec3 | null {
  return useMemo(() => {
    const row = groups.flatMap((group) => group.rows).find((candidate) => candidate.id === selectedRowId);
    if (!row || row.nodeIds.length > MOST_PIECES_TO_FLY_TO) return null;
    return rowCenter(scene, row);
  }, [scene, groups, selectedRowId]);
}

function subscribeToWidth(onChange: () => void) {
  const query = window.matchMedia(WIDE_SCREEN);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function useWideScreen(): boolean {
  return useSyncExternalStore(subscribeToWidth, () => window.matchMedia(WIDE_SCREEN).matches, () => false);
}

/** The steps that read the shop rather than work on it; the others use the model for picking, dragging or driving. */
const PANELS_SHOWING_FOUND = new Set(["answers", "results", "follow_ups", "waiting", "failed"]);

export function showsFound(panel: string): boolean {
  return PANELS_SHOWING_FOUND.has(panel);
}

type InModel = { found: FoundHandles | null; foundFocus: Vec3 | null; frameShift: number };

/** What the model draws of the found pieces on this step: nothing at all on the steps that don't show them. */
export function foundInModel(found: FoundObjects, shown: boolean): InModel {
  return shown ? { found: found.handles, foundFocus: found.focus, frameShift: found.frameShift } : { found: null, foundFocus: null, frameShift: 0 };
}

function useSelection(rowOfNode: Map<string, string>, onPick: () => void) {
  const [selectedRowId, setSelectedRowId] = useState<string | null>(null);
  const pickNode = useCallback((nodeId: string) => {
    const rowId = rowOfNode.get(nodeId);
    if (rowId) {
      setSelectedRowId(rowId);
      onPick();
    }
    return rowId !== undefined;
  }, [rowOfNode, onPick]);
  const toggleRow = useCallback((rowId: string) => {
    onPick();
    setSelectedRowId((current) => (current === rowId ? null : rowId));
  }, [onPick]);
  const clear = useCallback(() => setSelectedRowId(null), []);
  return { selectedRowId, pickNode, toggleRow, clear };
}

function useHover(rowOfNode: Map<string, string>) {
  const [hoveredRowId, setHoveredRowId] = useState<string | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const hoverNode = useCallback((nodeId: string | null) => {
    setHoveredNodeId(nodeId);
    setHoveredRowId(nodeId === null ? null : rowOfNode.get(nodeId) ?? null);
  }, [rowOfNode]);
  const hoverRow = useCallback((rowId: string | null) => {
    setHoveredNodeId(null);
    setHoveredRowId(rowId);
  }, []);
  return { hoveredRowId, hoveredNodeId, hoverNode, hoverRow };
}

/**
 * The pieces the scan found, and which of them the owner is pointing at, shared
 * by the list and the 3D view. Picking one lets go of any picked finding, so the
 * camera and the outline never answer two questions at once.
 */
export function useFoundObjects(scene: SceneGraph, onPick: () => void): FoundObjects {
  const groups = useMemo(() => foundGroups(scene), [scene]);
  const marks = useMemo(() => foundMarks(scene, groups), [scene, groups]);
  const rowOfNode = useMemo(() => new Map(marks.map((mark) => [mark.nodeId, mark.rowId])), [marks]);
  const hover = useHover(rowOfNode);
  const selection = useSelection(rowOfNode, onPick);
  const [legendOpen, setLegendOpen] = useState(true);
  const legendCovers = useWideScreen() && legendOpen && groups.length > 0;

  const { pickNode } = selection;
  const handles = useMemo<FoundHandles>(() => ({
    marks, hoveredRowId: hover.hoveredRowId, selectedRowId: selection.selectedRowId, hoveredNodeId: hover.hoveredNodeId,
    onHoverNode: hover.hoverNode, onPickNode: (nodeId) => { pickNode(nodeId); },
  }), [marks, hover.hoveredRowId, selection.selectedRowId, hover.hoveredNodeId, hover.hoverNode, pickNode]);

  return {
    groups, handles, hoveredRowId: hover.hoveredRowId, hoverRow: hover.hoverRow, ...selection,
    focus: useFocus(scene, groups, selection.selectedRowId),
    legendOpen, setLegendOpen, frameShift: legendCovers ? LEGEND_INSET_PX / 2 : 0,
  };
}
