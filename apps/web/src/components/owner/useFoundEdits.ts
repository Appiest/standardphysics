"use client";

import { useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import { suggestedNames } from "@/lib/found-objects";
import { editObject, type ObjectEdit, removeObject, restoreObject } from "@/lib/owner-client";
import type { PieceEditing } from "./FoundPieceEditor";
import type { FoundObjects } from "./useFoundObjects";

/** The piece the owner just removed, kept so they can put it back. */
export type RemovedPiece = { nodeId: string; name: string; fromRevision: number };

export type FoundEdits = {
  save: (nodeId: string, edit: ObjectEdit) => Promise<boolean>;
  remove: (nodeId: string, name: string) => Promise<boolean>;
  putBack: () => Promise<void>;
  dismissRemoved: () => void;
  removed: RemovedPiece | null;
  busy: boolean;
  problem: string | null;
};

const DID_NOT_SAVE = "That didn't save. Try again in a moment.";

/**
 * Renaming, regrouping and removing found pieces. Each change is a new revision
 * of the shop on the server, which checks it again; the page then reloads it.
 */
export function useFoundEdits(scanId: string, revision: number): FoundEdits {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [removed, setRemoved] = useState<RemovedPiece | null>(null);

  const attempt = useCallback(async (change: () => Promise<void>) => {
    setBusy(true);
    setProblem(null);
    try {
      await change();
      router.refresh();
      return true;
    } catch {
      setProblem(DID_NOT_SAVE);
      return false;
    } finally {
      setBusy(false);
    }
  }, [router]);

  const save = useCallback((nodeId: string, edit: ObjectEdit) => attempt(async () => {
    await editObject(scanId, revision, nodeId, edit);
    setRemoved(null);
  }), [attempt, scanId, revision]);

  const remove = useCallback((nodeId: string, name: string) => attempt(async () => {
    await removeObject(scanId, revision, nodeId);
    setRemoved({ nodeId, name, fromRevision: revision });
  }), [attempt, scanId, revision]);

  const putBack = useCallback(async () => {
    if (!removed) return;
    const done = await attempt(async () => {
      await restoreObject(scanId, revision, removed.nodeId, removed.fromRevision);
    });
    if (done) setRemoved(null);
  }, [attempt, scanId, revision, removed]);

  return { save, remove, putBack, dismissRemoved: () => setRemoved(null), removed, busy, problem };
}

/**
 * Editing for the found list, or nothing where it is only for reading: a shared
 * report, and while a layout is tried, whose moves are drawn on the revision it started from.
 */
export function usePieceEditing(found: FoundObjects, scanId: string, revision: number, { readOnly, trying }: { readOnly: boolean; trying: boolean }): PieceEditing | null {
  const edits = useFoundEdits(scanId, revision);
  const suggestions = useMemo(() => suggestedNames(found.groups), [found.groups]);
  if (readOnly || trying) return null;
  return {
    edits, suggestions, selectedNodeId: found.selectedNodeId,
    pickNode: (nodeId) => { found.pickNode(nodeId); }, hoverPiece: found.handles.onHoverNode, clear: found.clear,
  };
}
