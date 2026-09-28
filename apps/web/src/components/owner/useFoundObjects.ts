"use client";

import { useCallback, useMemo, useState, useSyncExternalStore } from "react";
import type { FoundHandles } from "@/components/workspace/FoundOutlines";
import { type FoundGroup, type FoundRow, foundGroups, foundMarks, rowCenter } from "@/lib/found-objects";
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
  /** The one piece picked, from its row or by tapping it in the model; null while a row of several is open. */
  selectedNodeId: string | null;
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

type Framing = {
  /** The camera's target for a row, or nothing when the row is too spread out to frame. */
  ofRow: (rowId: string) => Vec3 | null;
  ofPiece: (nodeId: string) => Vec3 | null;
  /** The piece a row stands for when it has only one. */
  onlyPiece: (rowId: string) => string | null;
};

function useFraming(scene: SceneGraph, groups: FoundGroup[]): Framing {
  return useMemo(() => {
    const rows = new Map<string, FoundRow>(groups.flatMap((group) => group.rows.map((row) => [row.id, row])));
    const ofRow = (rowId: string) => {
      const row = rows.get(rowId);
      return !row || row.nodeIds.length > MOST_PIECES_TO_FLY_TO ? null : rowCenter(scene, row);
    };
    const onlyPiece = (rowId: string) => {
      const nodeIds = rows.get(rowId)?.nodeIds ?? [];
      return nodeIds.length === 1 ? nodeIds[0] : null;
    };
    return { ofRow, ofPiece: (nodeId: string) => rowCenter(scene, { nodeIds: [nodeId] }), onlyPiece };
  }, [scene, groups]);
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
const PANELS_SHOWING_FOUND = new Set(["answers", "results", "waiting", "failed"]);

export function showsFound(panel: string): boolean {
  return PANELS_SHOWING_FOUND.has(panel);
}

type InModel = { found: FoundHandles | null; foundFocus: Vec3 | null; frameShift: number };

/** What the model draws of the found pieces on this step: nothing at all on the steps that don't show them. */
export function foundInModel(found: FoundObjects, shown: boolean): InModel {
  return shown ? { found: found.handles, foundFocus: found.focus, frameShift: found.frameShift } : { found: null, foundFocus: null, frameShift: 0 };
}

/**
 * While a layout is tried, only the rows the owner points at are drawn, so every
 * other piece stays free to grab. The legend isn't over the model then, so nothing shifts.
 */
export function pointedInModel(found: FoundObjects): InModel {
  const pointed = new Set([found.hoveredRowId, found.selectedRowId]);
  const marks = found.handles.marks.filter((mark) => pointed.has(mark.rowId));
  return { found: { ...found.handles, marks }, foundFocus: found.focus, frameShift: 0 };
}

type Selection = { rowId: string; nodeId: string | null; focus: Vec3 | null };

/**
 * The camera's target is fixed when the row or piece is picked, so dragging one
 * of its pieces in a tried layout doesn't pull the camera along behind it. A
 * picked piece keeps its selection when renaming files it under another row.
 */
function useSelection(rowOfNode: Map<string, string>, framing: Framing, onPick: () => void) {
  const [selection, setSelection] = useState<Selection | null>(null);
  const pickNode = useCallback((nodeId: string) => {
    const rowId = rowOfNode.get(nodeId);
    if (rowId) {
      setSelection({ rowId, nodeId, focus: framing.ofPiece(nodeId) });
      onPick();
    }
    return rowId !== undefined;
  }, [rowOfNode, framing, onPick]);
  const toggleRow = useCallback((rowId: string) => {
    onPick();
    setSelection((current) => (current?.rowId === rowId ? null : { rowId, nodeId: framing.onlyPiece(rowId), focus: framing.ofRow(rowId) }));
  }, [framing, onPick]);
  const clear = useCallback(() => setSelection(null), []);
  return { ...selected(selection, rowOfNode), pickNode, toggleRow, clear };
}

/** The row follows the picked piece, so renaming it into another row keeps it open. */
function selected(selection: Selection | null, rowOfNode: Map<string, string>) {
  if (!selection) return { selectedRowId: null, selectedNodeId: null, focus: null };
  const movedTo = selection.nodeId ? rowOfNode.get(selection.nodeId) : undefined;
  return { selectedRowId: movedTo ?? selection.rowId, selectedNodeId: selection.nodeId, focus: selection.focus };
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
  const selection = useSelection(rowOfNode, useFraming(scene, groups), onPick);
  const [legendOpen, setLegendOpen] = useState(true);
  const legendCovers = useWideScreen() && legendOpen && groups.length > 0;

  const { pickNode } = selection;
  const handles = useMemo<FoundHandles>(() => ({
    marks, hoveredRowId: hover.hoveredRowId, selectedRowId: selection.selectedRowId, selectedNodeId: selection.selectedNodeId,
    hoveredNodeId: hover.hoveredNodeId, onHoverNode: hover.hoverNode, onPickNode: (nodeId) => { pickNode(nodeId); },
  }), [marks, hover.hoveredRowId, selection.selectedRowId, selection.selectedNodeId, hover.hoveredNodeId, hover.hoverNode, pickNode]);

  return {
    groups, handles, hoveredRowId: hover.hoveredRowId, hoverRow: hover.hoverRow, ...selection,
    legendOpen, setLegendOpen, frameShift: legendCovers ? LEGEND_INSET_PX / 2 : 0,
  };
}
