"use client";

import { Camera, X } from "@phosphor-icons/react";
import { useCallback, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { markObservation, frameUrl } from "@/lib/review-client";
import { isUsablePointerBox, pointerBoxLabel, sensorBoxFromPointer, type PointerBox } from "@/lib/sensor-box";
import { TARGET_CLASS_LABEL, type TargetClass } from "@/lib/review-targets";
import type { SceneGraph } from "@/types/contracts";

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

function FrameMissing() {
  return (
    <p role="status" className="rounded-lg bg-rule/30 p-3 text-xs text-ink-muted">
      Original photographs are not served from this server yet, so there is no frame to draw on.
      Nothing is saved without real frame pixels. Open this scan on the phone to add close-up photos,
      or wait for the frames route before marking here.
    </p>
  );
}

function FrameSurface({ src, labelId, imageRef, box, onDown, onMove, onUp }: {
  src: string;
  labelId: string;
  imageRef: React.RefObject<HTMLImageElement | null>;
  box: PointerBox | null;
  onDown: (event: React.PointerEvent<HTMLImageElement>) => void;
  onMove: (event: React.PointerEvent<HTMLImageElement>) => void;
  onUp: () => void;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <p role="status" className="rounded-lg bg-rule/30 p-3 text-xs text-ink-muted">
        That frame could not be loaded. Check the frame identifier, or open this scan on the phone and recapture close-ups.
      </p>
    );
  }
  return (
    <div className="overflow-hidden rounded-xl bg-rule/40">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        ref={imageRef}
        src={src}
        alt={labelId}
        draggable={false}
        className="max-h-[46vh] w-full touch-none select-none object-contain"
        onError={() => setFailed(true)}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
      />
      {box && isUsablePointerBox(box) && (
        <p className="px-2 py-1 text-[11px] text-ink-muted">
          {pointerBoxLabel(box)}. Saved marks keep this spot as photographed evidence.
        </p>
      )}
    </div>
  );
}

/**
 * A person points at the pixels of a target the pipeline missed.
 *
 * The frame shown is an original stored photograph; the box is reported in the
 * frame's own pixels. Attaching a measured object gives the mark that object's
 * measured position; a mark without one stays unlocalized and is never given a
 * position invented from the click.
 */
export function MarkInPhoto({ scanId, revision, targetClass, suggestedNodeId, onClose, onSaved }: MarkInPhotoProps) {
  const [saving, setSaving] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [frameId, setFrameId] = useState("latest");
  const [attachNode, setAttachNode] = useState(suggestedNodeId !== null);
  const [note, setNote] = useState("");
  const [box, setBox] = useState<PointerBox | null>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);

  const canSubmit = box !== null && isUsablePointerBox(box) && !saving;

  const onPointerDown = useCallback((event: React.PointerEvent<HTMLImageElement>) => {
    event.preventDefault();
    dragStart.current = { x: event.clientX, y: event.clientY };
    event.currentTarget.setPointerCapture(event.pointerId);
  }, []);

  const onPointerMove = useCallback((event: React.PointerEvent<HTMLImageElement>) => {
    if (!dragStart.current || !imageRef.current) return;
    const natural = { width: imageRef.current.naturalWidth, height: imageRef.current.naturalHeight };
    const rect = imageRef.current.getBoundingClientRect();
    if (natural.width === 0 || natural.height === 0) return;
    setBox(sensorBoxFromPointer(dragStart.current, { x: event.clientX, y: event.clientY }, rect, natural));
  }, []);

  const onPointerUp = useCallback(() => {
    dragStart.current = null;
  }, []);

  const save = useCallback(async () => {
    if (!box || !isUsablePointerBox(box)) return;
    setSaving(true);
    setRefusal(null);
    try {
      const saved = await markObservation(scanId, revision, {
        target_class: targetClass,
        frame_id: frameId,
        sensor_box: [box.left, box.top, box.right, box.bottom],
        node_id: attachNode ? suggestedNodeId : null,
        review_status: "candidate",
        note: note.length > 0 ? note : null,
      });
      onSaved(saved);
    } catch (error) {
      const status = (error as { status?: number }).status;
      setSaving(false);
      setRefusal(status === 409
        ? "This scan changed while you were marking. Close this, reload, and mark again on the latest revision."
        : "The mark did not save. Try again in a moment.");
    }
  }, [box, scanId, revision, targetClass, frameId, attachNode, suggestedNodeId, note, onSaved]);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-ink/40 p-3 sm:items-center" role="dialog" aria-modal="true" aria-label={`Mark a ${TARGET_CLASS_LABEL[targetClass]} in a photo`}>
      <div className="flex max-h-[92dvh] w-full max-w-md flex-col gap-3 overflow-y-auto rounded-2xl bg-sheet p-4 shadow-float">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-base font-semibold text-ink">
            <Camera size={18} aria-hidden />
            Mark a {TARGET_CLASS_LABEL[targetClass].toLowerCase()} in a photo
          </h2>
          <button type="button" onClick={onClose} aria-label="Close marking" className="grid size-9 place-items-center rounded-lg text-ink-muted hover:bg-ink/5 hover:text-ink">
            <X size={18} aria-hidden />
          </button>
        </div>

        <label className="grid gap-1 text-xs font-medium text-ink-muted">
          Original frame to mark
          <div className="flex items-center gap-1">
            <input
              type="text"
              value={frameId}
              onChange={(event) => { setFrameId(event.target.value); setBox(null); }}
              className="min-w-0 flex-1 rounded-lg border border-rule bg-sheet px-2 py-2 text-sm text-ink"
              aria-label="Frame identifier"
            />
            <Button variant="quiet" onClick={() => setBox(null)}>Reload</Button>
          </div>
        </label>

        {!frameId.trim() ? (
          <FrameMissing />
        ) : (
          <FrameSurface
            src={frameUrl(scanId, frameId)}
            labelId={`Stored photograph of the room, for marking the ${TARGET_CLASS_LABEL[targetClass].toLowerCase()}`}
            imageRef={imageRef}
            box={box}
            onDown={onPointerDown}
            onMove={onPointerMove}
            onUp={onPointerUp}
          />
        )}

        <label className="flex items-start gap-2 text-xs text-ink-muted">
          <input
            type="checkbox"
            checked={attachNode}
            onChange={(event) => setAttachNode(event.target.checked)}
            disabled={suggestedNodeId === null}
            className="mt-0.5 accent-accent"
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
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!canSubmit} onClick={() => void save()}>
            Save manual mark
          </Button>
        </div>
      </div>
    </div>
  );
}
