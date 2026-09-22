"use client";

import { Camera, X } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { getFrames, manualMarkRefusal, markObservation, type FrameEntry } from "@/lib/review-client";
import { isUsablePointerBox, pointerBoxLabel, sensorBoxFromPointer, type PointerBox } from "@/lib/sensor-box";
import { TARGET_CLASS_LABEL, type TargetClass } from "@/lib/review-targets";
import type { SceneGraph } from "@/types/contracts";

type ListState = "loading" | "ready" | "unavailable" | "failed";

type MarkInPhotoProps = {
  scanId: string;
  /** The revision the mark is based on; a newer revision answers 409, which this shows. */
  revision: number;
  targetClass: TargetClass;
  /** A measured object to attach the mark to, when the marking user picks the node. */
  suggestedNodeId: string | null;
  onClose: () => void;
  onSaved: (scene: SceneGraph) => void;
};

function FrameThumb({ frame, selected, onChoose }: { frame: FrameEntry; selected: boolean; onChoose: () => void }) {
  const [broken, setBroken] = useState(false);
  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={`Photo ${frame.frame_id}`}
      onClick={onChoose}
      className={`grid aspect-square min-h-11 w-full place-items-center overflow-hidden rounded-lg border transition-colors ${
        selected ? "border-accent ring-2 ring-accent/40" : "border-rule/60 bg-rule/30 hover:bg-rule/50"
      }`}
    >
      {broken ? (
        <X size={18} className="text-ink-faint" aria-hidden />
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={frame.image_url}
          alt=""
          aria-hidden
          loading="lazy"
          onError={() => setBroken(true)}
          className="h-full w-full object-cover"
        />
      )}
    </button>
  );
}

function DrawFrame({ frame, alt, surfaceRef, onDown, onMove, onUp }: {
  frame: FrameEntry;
  alt: string;
  surfaceRef: React.RefObject<HTMLImageElement | null>;
  onDown: (event: React.PointerEvent<HTMLImageElement>) => void;
  onMove: (event: React.PointerEvent<HTMLImageElement>) => void;
  onUp: () => void;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <p role="status" className="p-3 text-xs text-ink-muted">
        This photo could not be loaded from the server. Pick another one.
      </p>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      ref={surfaceRef}
      src={frame.image_url}
      alt={alt}
      draggable={false}
      className="max-h-[42vh] w-full touch-none select-none object-contain lg:max-h-[50vh]"
      onError={() => setFailed(true)}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
    />
  );
}

function ListStatus({ listState, listingError, onRetry }: { listState: ListState; listingError: string | null; onRetry: () => void }) {
  if (listState === "loading") {
    return <p role="status" className="text-sm text-ink-muted">Loading the photos from your scan…</p>;
  }
  if (listState === "unavailable") {
    return (
      <p role="status" className="rounded-lg bg-rule/30 p-3 text-xs text-ink-muted">
        This server does not serve a photo list yet, so there is no frame to draw on. Nothing is saved without real
        frame pixels. Your photos are still stored with the scan; update the server&apos;s review routes, then try again.
      </p>
    );
  }
  if (listState === "failed") {
    return (
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-rose-50 p-3 text-xs text-problem">
        <span role="alert">{listingError}</span>
        <Button variant="quiet" onClick={onRetry}>Try loading again</Button>
      </div>
    );
  }
  return null;
}

function FramePicker({ frames, unreadableCount, targetLabel, selected, box, listRef, surfaceRef, onChoose, onDown, onMove, onUp }: {
  frames: FrameEntry[];
  unreadableCount: number;
  targetLabel: string;
  selected: FrameEntry | null;
  box: PointerBox | null;
  listRef: React.RefObject<HTMLDivElement | null>;
  surfaceRef: React.RefObject<HTMLImageElement | null>;
  onChoose: (frame: FrameEntry) => void;
  onDown: (event: React.PointerEvent<HTMLImageElement>) => void;
  onMove: (event: React.PointerEvent<HTMLImageElement>) => void;
  onUp: () => void;
}) {
  if (frames.length === 0) {
    return (
      <p role="status" className="rounded-lg bg-rule/30 p-3 text-xs text-ink-muted">
        This scan has no readable photographs to mark in. Take more photos in the app, upload them, then try again.
      </p>
    );
  }
  return (
    <>
      <p className="text-xs text-ink-muted">
        Pick the photo that shows the {targetLabel}, then drag a box around it.
      </p>
      {unreadableCount > 0 && (
        <p className="text-[11px] text-amber-800 dark:text-amber-300">
          {unreadableCount} stored photo{unreadableCount === 1 ? "" : "s"} could not be read on the server and are not shown.
        </p>
      )}
      <div ref={listRef} role="group" aria-label="Your scan photos" className="grid max-h-40 grid-cols-4 gap-2 overflow-y-auto pr-1 sm:grid-cols-6">
        {frames.map((frame, index) => (
          <div key={frame.frame_id} className="shrink-0">
            <FrameThumb frame={frame} selected={frame.frame_id === selected?.frame_id} onChoose={() => onChoose(frame)} />
            <p className="mt-0.5 text-center text-[10px] text-ink-muted">{index + 1}</p>
          </div>
        ))}
      </div>
      {selected === null ? (
        <p className="text-xs text-ink-muted">Choose a photo above to start marking.</p>
      ) : (
        <div className="overflow-hidden rounded-xl bg-rule/40">
          <DrawFrame
            key={selected.frame_id}
            frame={selected}
            alt={`Photo ${selected.frame_id}. Drag a box around the ${targetLabel} with your finger`}
            surfaceRef={surfaceRef}
            onDown={onDown}
            onMove={onMove}
            onUp={onUp}
          />
          {box && isUsablePointerBox(box) && (
            <p className="px-2 py-1 text-[11px] text-ink-muted">
              {pointerBoxLabel(box)}. Saved marks keep this spot as photographed evidence.
            </p>
          )}
        </div>
      )}
    </>
  );
}

function MarkForm({ suggestedNodeId, attachNode, setAttachNode, note, setNote, saving, refusal, canSubmit, onClose, onSave }: {
  suggestedNodeId: string | null;
  attachNode: boolean;
  setAttachNode: (value: boolean) => void;
  note: string;
  setNote: (value: string) => void;
  saving: boolean;
  refusal: string | null;
  canSubmit: boolean;
  onClose: () => void;
  onSave: () => void;
}) {
  return (
    <>
      <label className="flex items-start gap-2 text-xs text-ink-muted">
        <input
          type="checkbox"
          checked={attachNode}
          onChange={(event) => setAttachNode(event.target.checked)}
          disabled={suggestedNodeId === null}
          className="mt-0.5 size-4 shrink-0 accent-accent"
        />
        <span>
          {suggestedNodeId === null
            ? "No measured object selected. The mark stays photo-only with no position on the map."
            : "Attach to the measured object: it keeps that object's measured position. The mark is never moved to where you clicked."}
        </span>
      </label>

      <label className="grid gap-1 text-xs font-medium text-ink-muted">
        Note (optional)
        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          maxLength={500}
          rows={2}
          placeholder="What you can see in this spot"
          className="w-full resize-none rounded-lg border border-rule bg-sheet px-2 py-2 text-sm text-ink"
        />
      </label>

      {saving && <p role="status" className="text-xs text-ink-muted">Saving your mark…</p>}
      {refusal && <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-problem">{refusal}</p>}

      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="quiet" onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={!canSubmit} onClick={onSave}>
          Save manual mark
        </Button>
      </div>
    </>
  );
}

/**
 * A person picks one of their scan's real photographs and points at the pixels
 * of a target the pipeline missed. No invented frame identity exists: every
 * selectable photo arrived here from the owner-authenticated frame list.
 */
export function MarkInPhoto({ scanId, revision, targetClass, suggestedNodeId, onClose, onSaved }: MarkInPhotoProps) {
  const [listState, setListState] = useState<ListState>("loading");
  const [frames, setFrames] = useState<FrameEntry[]>([]);
  const [unreadable, setUnreadable] = useState<string[]>([]);
  const [listingError, setListingError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [box, setBox] = useState<PointerBox | null>(null);
  const [saving, setSaving] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [attachNode, setAttachNode] = useState(suggestedNodeId !== null);
  const [note, setNote] = useState("");
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const surfaceRef = useRef<HTMLImageElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);

  const selected = frames.find((frame) => frame.frame_id === selectedId) ?? null;
  const targetLabel = TARGET_CLASS_LABEL[targetClass].toLowerCase();

  const load = useCallback(async () => {
    setListState("loading");
    setListingError(null);
    setSelectedId(null);
    setBox(null);
    try {
      const listing = await getFrames(scanId);
      if (listing === null) {
        setFrames([]);
        setUnreadable([]);
        setListState("unavailable");
        return;
      }
      setFrames(listing.frames);
      setUnreadable(listing.unreadable);
      setListState("ready");
      requestAnimationFrame(() => listRef.current?.querySelector<HTMLButtonElement>("button")?.focus());
    } catch (error) {
      setFrames([]);
      setUnreadable([]);
      setListingError((error as { error?: string }).error ?? "The photo list could not be read. Try again.");
      setListState("failed");
    }
  }, [scanId]);

  useEffect(() => {
    const frame = requestAnimationFrame(() => { void load(); });
    closeRef.current?.focus();
    return () => cancelAnimationFrame(frame);
  }, [load]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const choose = useCallback((frame: FrameEntry) => {
    setSelectedId(frame.frame_id);
    setBox(null);
  }, []);

  const onPointerDown = useCallback((event: React.PointerEvent<HTMLImageElement>) => {
    event.preventDefault();
    dragStart.current = { x: event.clientX, y: event.clientY };
    event.currentTarget.setPointerCapture(event.pointerId);
  }, []);

  const onPointerMove = useCallback((event: React.PointerEvent<HTMLImageElement>) => {
    if (!dragStart.current || !surfaceRef.current || selected === null) return;
    const natural = { width: surfaceRef.current.naturalWidth, height: surfaceRef.current.naturalHeight };
    const rect = surfaceRef.current.getBoundingClientRect();
    if (natural.width === 0 || natural.height === 0) return;
    setBox(sensorBoxFromPointer(dragStart.current, { x: event.clientX, y: event.clientY }, rect, natural));
  }, [selected]);

  const onPointerEnd = useCallback(() => {
    dragStart.current = null;
  }, []);

  const canSubmit = selected !== null && box !== null && isUsablePointerBox(box) && !saving;

  const save = useCallback(async () => {
    if (!selected || !box || !isUsablePointerBox(box)) return;
    setSaving(true);
    setRefusal(null);
    try {
      const saved = await markObservation(scanId, revision, {
        target_class: targetClass,
        frame_id: selected.frame_id,
        sensor_box: [box.left, box.top, box.right, box.bottom],
        node_id: attachNode ? suggestedNodeId : null,
        review_status: "candidate",
        note: note.length > 0 ? note : null,
      });
      onSaved(saved);
    } catch (error) {
      setSaving(false);
      setRefusal(manualMarkRefusal(error));
    }
  }, [selected, box, scanId, revision, targetClass, attachNode, suggestedNodeId, note, onSaved]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-ink/40 p-2 sm:items-center sm:p-3"
      role="dialog"
      aria-modal="true"
      aria-label={`Mark a ${targetLabel} in one of your photos`}
    >
      <div className="flex max-h-[94dvh] w-full max-w-lg flex-col gap-3 overflow-y-auto rounded-2xl bg-sheet p-3 shadow-float sm:p-4">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-base font-semibold text-ink">
            <Camera size={18} aria-hidden />
            Mark a {targetLabel} in a photo
          </h2>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="Close marking" className="grid size-11 place-items-center rounded-lg text-ink-muted hover:bg-ink/5 hover:text-ink">
            <X size={18} aria-hidden />
          </button>
        </div>

        <ListStatus listState={listState} listingError={listingError} onRetry={() => void load()} />

        {listState === "ready" && (
          <>
            <FramePicker
              frames={frames}
              unreadableCount={unreadable.length}
              targetLabel={targetLabel}
              selected={selected}
              box={box}
              listRef={listRef}
              surfaceRef={surfaceRef}
              onChoose={choose}
              onDown={onPointerDown}
              onMove={onPointerMove}
              onUp={onPointerEnd}
            />
            <MarkForm
              suggestedNodeId={suggestedNodeId}
              attachNode={attachNode}
              setAttachNode={setAttachNode}
              note={note}
              setNote={setNote}
              saving={saving}
              refusal={refusal}
              canSubmit={canSubmit}
              onClose={onClose}
              onSave={() => void save()}
            />
          </>
        )}
      </div>
    </div>
  );
}
