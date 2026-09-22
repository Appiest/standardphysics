"use client";

import {
  Camera,
  CheckCircle,
  Lightning,
  MapPin,
  Monitor,
  Question,
  Storefront,
  ThumbsDown,
  ThumbsUp,
  Toilet,
} from "@phosphor-icons/react";
import { useCallback, useMemo, useState } from "react";
import { Button } from "@/components/ui/Button";
import {
  classEmptyDetail,
  firstRenderableCrop,
  nodePosition,
  reviewEntriesFor,
  TARGET_CLASSES,
  TARGET_CLASS_LABEL,
  type ReviewEntry,
  type TargetClass,
  type TargetClassFilter,
} from "@/lib/review-targets";
import { cropUrl, reviewAttachment } from "@/lib/review-client";
import type { ObservationCrop, SceneGraph, SceneNode } from "@/types/contracts";
import { MarkInPhoto } from "./MarkInPhoto";

const REVIEW_CHIP: Record<string, { label: string; className: string }> = {
  detected: { label: "Observed", className: "bg-sky-100 text-sky-800" },
  candidate: { label: "Candidate", className: "bg-amber-100 text-amber-800" },
  confirmed_by_user: { label: "Confirmed", className: "bg-emerald-100 text-emerald-800" },
  rejected_by_user: { label: "Flagged wrong", className: "bg-rose-100 text-rose-800" },
};

const LOCALIZATION_LABEL: Record<string, string> = {
  verified_support: "Position verified against a measured surface",
  inferred_plane: "Approximate position, inferred from a surface plane",
  unanchored: "Photo evidence only, position not measured",
  needs_verification: "Position needs verification",
};

const CLASS_ICON: Record<TargetClass, typeof Lightning> = {
  outlet: Lightning,
  television: Monitor,
  service_counter: Storefront,
  restroom_entrance: Toilet,
};

function statusChip(status: string) {
  const chip = REVIEW_CHIP[status];
  if (!chip) return null;
  return <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${chip.className}`}>{chip.label}</span>;
}

function positionText(position: { x: number; y: number; z: number } | null, quality: string | undefined): string {
  if (!position) return quality ? LOCALIZATION_LABEL[quality] ?? "Position unknown" : "Position unknown";
  const support = LOCALIZATION_LABEL[quality ?? "unanchored"]?.split(",")[0].toLowerCase() ?? "position";
  return `At about ${position.x.toFixed(1)}, ${position.y.toFixed(1)} m (${support})`;
}

function EvidenceCrop({ scanId, cropId, alt }: { scanId: string; cropId: string; alt: string }) {
  const [broken, setBroken] = useState(false);
  if (broken) {
    return (
      <p role="status" className="flex h-20 items-center justify-center rounded-lg bg-rule/30 px-3 text-center text-xs text-ink-muted">
        This crop is not available. Take another photo of the spot, or mark it again in a photo.
      </p>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={cropUrl(scanId, cropId)}
      alt={alt}
      onError={() => setBroken(true)}
      className="aspect-square h-24 w-full rounded-lg bg-rule/40 object-cover"
    />
  );
}

function MarkButton({ scanId, scene, targetClass, onPersisted, currentNodeId }: {
  scanId: string;
  scene: SceneGraph;
  targetClass: TargetClass;
  onPersisted?: (scene: SceneGraph) => void;
  currentNodeId: string | null;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="quiet" className="text-xs text-accent" onClick={() => setOpen(true)}>
        <Camera size={14} aria-hidden />
        Mark in a photo
      </Button>
      {open && (
        <MarkInPhoto
          scanId={scanId}
          revision={scene.revision}
          targetClass={targetClass}
          suggestedNodeId={currentNodeId}
          onClose={() => setOpen(false)}
          onSaved={(saved) => {
            setOpen(false);
            onPersisted?.(saved);
          }}
        />
      )}
    </>
  );
}

function ObservationNotes({ observations }: { observations: ObservationCrop[] }) {
  if (observations.length === 0) return null;
  return (
    <div className="grid gap-1 rounded-lg bg-rule/30 p-2 text-[11px] text-ink-muted">
      {observations.map((candidate, index) => (
        <p key={`${candidate.frame_id}-${index}`}>
          {candidate.provenance === "manual" ? "Manual mark" : "Automatic detection"} from frame {candidate.frame_id}
          {candidate.provenance === "manual" && candidate.marked_by ? `, marked by ${candidate.marked_by}` : ""}
        </p>
      ))}
    </div>
  );
}

function UncertaintyList({ reasons }: { reasons: string[] }) {
  if (reasons.length === 0) return null;
  return (
    <div className="rounded-lg bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
      <p className="font-semibold">Known uncertainty:</p>
      <ul className="mt-1 list-disc pl-4 text-[11px]">
        {reasons.map((reason, index) => <li key={index}>{reason}</li>)}
      </ul>
    </div>
  );
}

function UnlocalizedCard({ scanId, scene, entry, onPersisted }: {
  scanId: string;
  scene: SceneGraph;
  entry: ReviewEntry & { source: "unlocalized" };
  onPersisted?: (scene: SceneGraph) => void;
}) {
  const observation = entry.observation;
  return (
    <li className="rounded-lg border border-rule/60 bg-sheet/80 p-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="font-medium text-ink">{TARGET_CLASS_LABEL[entry.targetClass]}</p>
          <p className="text-xs text-ink-muted">Marked in a photo. No measured position was attached, so it is not shown on the map.</p>
        </div>
        {statusChip(observation.review_status)}
      </div>
      <p className="mt-2 text-[11px] text-ink-muted">
        Frame {observation.frame_id}
        {observation.provenance === "manual" ? " • Marked by you" : ""}
        {observation.marked_by ? ` • ${observation.marked_by}` : ""}
      </p>
      <p className="mt-1 text-[11px] text-ink-muted">
        Photo pixels {Math.round(observation.sensor_box[0])}–{Math.round(observation.sensor_box[2])} across,{" "}
        {Math.round(observation.sensor_box[1])}–{Math.round(observation.sensor_box[3])} down
      </p>
      {observation.note && <p className="mt-1 text-xs text-ink">{observation.note}</p>}
      <div className="mt-2 flex flex-wrap gap-2 border-t border-rule/60 pt-2">
        <MarkButton
          scanId={scanId}
          scene={scene}
          targetClass={entry.targetClass}
          currentNodeId={null}
          onPersisted={onPersisted}
        />
      </div>
    </li>
  );
}

function reviewRefusal(error: unknown): string {
  const statusCode = (error as { status?: number }).status;
  return statusCode === 409
    ? "This scan changed since you opened it. Reload and review the latest evidence."
    : "That didn't save. Try again in a moment.";
}

function SelectedDetails({ scanId, scene, entry, onPersisted }: {
  scanId: string;
  scene: SceneGraph;
  entry: ReviewEntry & { source: "node" };
  onPersisted?: (scene: SceneGraph) => void;
}) {
  const { node, targetClass, observations } = entry;
  const attachment = node.attachment;
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const crop = firstRenderableCrop(observations);

  const applyReview = useCallback(async (status: "confirmed_by_user" | "rejected_by_user") => {
    setSaving(true);
    setProblem(null);
    try {
      const saved = await reviewAttachment(scanId, scene.revision, node.id, status);
      onPersisted?.(saved);
      if (saved.revision !== scene.revision + 1) {
        setProblem("Someone else changed this scan at the same time. Reload to review the latest evidence.");
      }
    } catch (error) {
      setProblem(reviewRefusal(error));
    } finally {
      setSaving(false);
    }
  }, [scanId, scene.revision, node.id, onPersisted]);

  return (
    <div className="mt-2 flex flex-col gap-3">
      {crop ? (
        <EvidenceCrop scanId={scanId} cropId={crop.image_url!} alt={`Photographed evidence of the ${TARGET_CLASS_LABEL[targetClass].toLowerCase()}: ${node.label}`} />
      ) : (
        <p role="status" className="rounded-lg bg-rule/30 px-3 py-2 text-xs text-ink-muted">
          No photographed evidence crop is stored for this object. Mark it in a photo to attach source pixels.
        </p>
      )}
      <ObservationNotes observations={observations} />
      <UncertaintyList reasons={attachment?.uncertainty_reasons ?? []} />
      <div className="flex flex-wrap items-center gap-2 border-t border-rule/60 pt-2">
        <span className="text-[11px] text-ink-muted">Your review:</span>
        <Button variant="quiet" className="h-9 px-2 text-xs text-emerald-700" disabled={saving} onClick={() => void applyReview("confirmed_by_user")}>
          <ThumbsUp size={14} aria-hidden />
          Confirm
        </Button>
        <Button variant="quiet" className="h-9 px-2 text-xs text-rose-700" disabled={saving} onClick={() => void applyReview("rejected_by_user")}>
          <ThumbsDown size={14} aria-hidden />
          Flag wrong
        </Button>
        <MarkButton scanId={scanId} scene={scene} targetClass={targetClass} currentNodeId={node.id} onPersisted={onPersisted} />
      </div>
      {problem && <p role="alert" className="text-xs text-problem">{problem}</p>}
    </div>
  );
}

function NodeEntryCard({ scanId, scene, entry, selected, onSelect, onPersisted }: {
  scanId: string;
  scene: SceneGraph;
  entry: ReviewEntry & { source: "node" };
  selected: boolean;
  onSelect: () => void;
  onPersisted?: (scene: SceneGraph) => void;
}) {
  const node: SceneNode = entry.node;
  const attachment = node.attachment;
  const position = nodePosition(node);

  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        className={`flex w-full items-center gap-3 rounded-lg border p-2.5 text-left transition-colors ${
          selected ? "border-sky-500 bg-sky-50/50 shadow-sm dark:bg-sky-950/20" : "border-rule/60 bg-sheet/80 hover:bg-sheet"
        }`}
      >
        <MapPin size={16} className={`size-4 shrink-0 ${selected ? "text-sky-600" : "text-ink-muted"}`} aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="flex items-center justify-between gap-2">
            <span className="truncate font-medium text-ink">{node.label}</span>
            {statusChip(attachment?.review_status ?? "detected")}
          </span>
          <span className="mt-0.5 block text-[11px] text-ink-muted">
            {positionText(position, attachment?.localization_quality)}
          </span>
        </span>
      </button>
      {selected && <SelectedDetails scanId={scanId} scene={scene} entry={entry} onPersisted={onPersisted} />}
    </li>
  );
}

function EmptyClass({ targetClass, scanId, scene, onPersisted }: {
  targetClass: TargetClass;
  scanId: string;
  scene: SceneGraph;
  onPersisted?: (scene: SceneGraph) => void;
}) {
  return (
    <div className="rounded-xl border border-dashed border-rule p-6 text-center text-ink-muted">
      <Question size={24} className="mx-auto mb-2 text-ink-muted/60" aria-hidden />
      <p className="font-medium">Unknown for this scan</p>
      <p className="mt-1 text-xs">{classEmptyDetail(targetClass)}</p>
      <div className="mt-3">
        <MarkButton scanId={scanId} scene={scene} targetClass={targetClass} currentNodeId={null} onPersisted={onPersisted} />
      </div>
    </div>
  );
}

function ReviewLegend() {
  return (
    <div className="rounded-lg bg-rule/20 p-2.5 text-[10px] text-ink-muted">
      <p className="flex items-center gap-1 font-semibold text-ink"><CheckCircle size={12} aria-hidden />What a review means here</p>
      <ul className="mt-1 list-disc pl-4">
        <li>Confirming records that you verified this object in its photo; it does not certify the object or anything about the room.</li>
        <li>Flagging wrong hides the automatic claim from later checks until evidence changes.</li>
        <li>Marking in a photo records your mark as manual evidence with your name and the frame it came from.</li>
      </ul>
    </div>
  );
}

export function ReviewPanel({ scanId, scene, selectedId, onSelectNode, onPersisted }: {
  scanId: string;
  scene: SceneGraph;
  selectedId: string | null;
  onSelectNode: (nodeId: string | null) => void;
  onPersisted?: (scene: SceneGraph) => void;
}) {
  const [filter, setFilter] = useState<TargetClassFilter>("outlet");
  const entries = useMemo(() => reviewEntriesFor(scene, filter), [scene, filter]);
  const counted = useMemo(() => {
    const counts = new Map<TargetClass, number>();
    for (const targetClass of TARGET_CLASSES) counts.set(targetClass, reviewEntriesFor(scene, targetClass).length);
    return counts;
  }, [scene]);
  const selectedEntry = useMemo(
    () => entries.find((entry) => entry.source === "node" && entry.node.id === selectedId) ?? null,
    [entries, selectedId]
  );

  return (
    <div className="flex flex-col gap-4 p-4 text-sm">
      <div className="flex items-center gap-2">
        <Camera size={18} className="text-accent" weight="bold" aria-hidden />
        <h2 className="text-base font-semibold text-ink">Review photographed objects</h2>
      </div>
      <p className="text-xs text-ink-muted">
        Each entry links its photographed evidence, its approximate measured position, and your review. Manual marks are labeled separately from automatic detections.
      </p>

      <div role="group" aria-label="Object type" className="flex max-w-full gap-1 overflow-x-auto rounded-xl bg-rule/50 p-1">
        {TARGET_CLASSES.map((targetClass) => {
          const Icon = CLASS_ICON[targetClass];
          const count = counted.get(targetClass) ?? 0;
          return (
            <Button
              key={targetClass}
              variant="chip"
              aria-pressed={filter === targetClass}
              onClick={() => setFilter(targetClass)}
              className="min-h-9"
            >
              <Icon size={14} weight={filter === targetClass ? "bold" : "regular"} aria-hidden />
              {TARGET_CLASS_LABEL[targetClass]}
              <span className="measurement text-xs opacity-70">{count}</span>
            </Button>
          );
        })}
      </div>

      {entries.length === 0 ? (
        <EmptyClass
          targetClass={filter === "all" ? "outlet" : filter}
          scanId={scanId}
          scene={scene}
          onPersisted={onPersisted}
        />
      ) : (
        <ul className="flex flex-col gap-1.5">
          {entries.map((entry) => {
            if (entry.source === "unlocalized") {
              return (
                <UnlocalizedCard
                  key={entry.observation.id}
                  scanId={scanId}
                  scene={scene}
                  entry={entry}
                  onPersisted={onPersisted}
                />
              );
            }
            return (
              <NodeEntryCard
                key={entry.node.id}
                scanId={scanId}
                scene={scene}
                entry={entry}
                selected={entry.node.id === selectedId}
                onSelect={() => onSelectNode(entry.node.id)}
                onPersisted={onPersisted}
              />
            );
          })}
        </ul>
      )}

      {selectedEntry === null && entries.length > 0 && selectedId !== null && (
        <p className="text-xs text-ink-muted">
          The selected object belongs to another type. Switch to its tab to review it.
        </p>
      )}

      <ReviewLegend />

      <p className="text-[11px] text-ink-muted">
        Items marked in a photo without a measured position stay out of the map and are shown above.
      </p>
    </div>
  );
}
