"use client";

import { Trash, X } from "@phosphor-icons/react";
import { type FormEvent, useId, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Field, SelectField } from "@/components/ui/Field";
import { IconButton } from "@/components/ui/IconButton";
import { draftChanges, FOUND_GROUPS, type FoundGroupId, type FoundRow, pieceName } from "@/lib/found-objects";
import type { FoundEdits } from "./useFoundEdits";

export type PieceEditing = {
  edits: FoundEdits;
  suggestions: string[];
  selectedNodeId: string | null;
  pickNode: (nodeId: string) => void;
  hoverPiece: (nodeId: string | null) => void;
  /** Lets go of the selection once the piece it pointed at is gone. */
  clear: () => void;
};

/** Under an open row: which of its pieces to change when it has several, then the form for the one picked. */
export function RowEditor({ row, editing }: { row: FoundRow; editing: PieceEditing }) {
  const nodeId = row.nodeIds.find((candidate) => candidate === editing.selectedNodeId) ?? null;
  return (
    <div className="mb-2 mt-1 flex flex-col gap-3 rounded-lg bg-ink/[0.04] p-3">
      {row.nodeIds.length > 1 && <PiecePicker row={row} editing={editing} />}
      {nodeId && <PieceForm key={`${nodeId}:${row.id}`} row={row} nodeId={nodeId} editing={editing} />}
    </div>
  );
}

/** One numbered button per piece; pointing at one lights it up in the model. */
function PiecePicker({ row, editing }: { row: FoundRow; editing: PieceEditing }) {
  const promptId = useId();
  return (
    <div className="flex flex-col gap-2">
      <p id={promptId} className="text-sm text-ink-muted">Pick which one to change, here or in the model</p>
      <ul aria-labelledby={promptId} className="flex flex-wrap gap-1">
        {row.nodeIds.map((nodeId, index) => (
          <li key={nodeId}>
            <button
              type="button"
              aria-label={pieceName(row, nodeId)}
              aria-pressed={nodeId === editing.selectedNodeId}
              onClick={() => editing.pickNode(nodeId)}
              onPointerEnter={() => editing.hoverPiece(nodeId)}
              onPointerLeave={() => editing.hoverPiece(null)}
              onFocus={() => editing.hoverPiece(nodeId)}
              onBlur={() => editing.hoverPiece(null)}
              className="pressable grid size-10 place-items-center rounded-lg bg-ink/[0.06] text-sm font-medium tabular-nums hover:bg-ink/[0.12] aria-pressed:bg-ink aria-pressed:text-paper"
            >
              {index + 1}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function PieceForm({ row, nodeId, editing }: { row: FoundRow; nodeId: string; editing: PieceEditing }) {
  const ids = { name: useId(), group: useId(), names: useId(), problem: useId() };
  const nameInput = useRef<HTMLInputElement>(null);
  const [name, setName] = useState(row.name);
  const [group, setGroup] = useState<FoundGroupId>(row.group);
  const [nameMissing, setNameMissing] = useState(false);
  const { edits } = editing;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (name.trim() === "") {
      setNameMissing(true);
      nameInput.current?.focus();
      return;
    }
    setNameMissing(false);
    const changes = draftChanges(row, { name, group });
    if (changes) await edits.save(nodeId, changes);
  };
  const remove = async () => {
    if (await edits.remove(nodeId, pieceName(row, nodeId))) editing.clear();
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-3" aria-describedby={edits.problem ? ids.problem : undefined}>
      <Field
        ref={nameInput} id={ids.name} label="What it is" list={ids.names} value={name} autoComplete="off"
        error={nameMissing ? "Give it a name, or remove it if it isn’t there." : undefined}
        onChange={(event) => setName(event.target.value)}
      />
      <datalist id={ids.names}>
        {editing.suggestions.map((suggestion) => <option key={suggestion} value={suggestion} />)}
      </datalist>
      <SelectField id={ids.group} label="Listed under" value={group} onChange={(event) => setGroup(event.target.value as FoundGroupId)}>
        {FOUND_GROUPS.map(({ id, title }) => <option key={id} value={id}>{title}</option>)}
      </SelectField>
      <p className="text-sm text-ink-muted">Saving checks your shop again. Your fixes also teach our scanner to name things better.</p>
      {edits.problem && <p id={ids.problem} role="alert" className="text-sm text-problem">{edits.problem}</p>}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button variant="danger" disabled={edits.busy} onClick={remove}>
          <Trash size={16} aria-hidden />
          It isn&rsquo;t here, remove it
        </Button>
        <Button type="submit" variant="primary" disabled={edits.busy}>
          {edits.busy ? "Saving" : "Save"}
        </Button>
      </div>
    </form>
  );
}

/** Says what was just removed, with the way to put it back, until the owner dismisses it. */
export function RemovedNotice({ edits }: { edits: FoundEdits }) {
  const { removed } = edits;
  return (
    <div role="status">
      {removed && (
        <div className="flex items-center gap-1 rounded-lg bg-ink py-1 pe-1 ps-3 text-sm text-paper">
          <span className="min-w-0 flex-1 text-pretty">Removed {removed.name.toLowerCase()}</span>
          <Button variant="inverse" disabled={edits.busy} onClick={() => void edits.putBack()}>Put it back</Button>
          <IconButton label="Close this note" tooltipSide="below" className="text-paper/70 hover:bg-paper/10 hover:text-paper" onClick={edits.dismissRemoved}>
            <X size={16} aria-hidden />
          </IconButton>
        </div>
      )}
    </div>
  );
}
