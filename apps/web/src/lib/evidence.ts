import type { EvidenceStatus, TextureStatus } from "@/types/contracts";
import { textureStatusView } from "./texture-status";

/** Four separate processing lanes. Each shows its own state and its own remedy. */
export type LaneId = "geometry" | "evidence" | "recognition" | "visual";

export type LaneAction = { label: string; kind: "recapture" | "retry" | "explain" };

export type LaneView = {
  id: LaneId;
  name: string;
  tone: "ok" | "working" | "needs_action" | "failed";
  headline: string;
  detail: string | null;
  action: LaneAction | null;
};

const RECAPTURE = { kind: "recapture", label: "What to recapture" } as const;
const EXPLAIN = { kind: "explain", label: "What's missing" } as const;

function joined(list: string[]): string {
  if (list.length === 0) return "nothing is missing on file";
  return list.join(", ");
}

/** Geometry: the measured room itself, independent of photos. */
function geometryLane(status: EvidenceStatus | null, measured: boolean): LaneView {
  if (status === null) {
    return {
      id: "geometry", name: "Measured room", tone: "needs_action",
      headline: measured ? "Room geometry available, evidence status unknown" : "Not measured yet",
      detail: "Keep scanning in the phone app until the room is measured.",
      action: RECAPTURE,
    };
  }
  if (status.geometry_state === "ready") {
    return { id: "geometry", name: "Measured room", tone: "ok", headline: "Room geometry ready", detail: null, action: null };
  }
  if (status.geometry_state === "failed") {
    return {
      id: "geometry", name: "Measured room", tone: "failed",
      headline: "The room did not measure",
      detail: "Scan the shop again; the saved photos stay with this scan.",
      action: RECAPTURE,
    };
  }
  return {
    id: "geometry", name: "Measured room", tone: "working",
    headline: "Measuring the room",
    detail: joined(status.missing_geometry_kinds),
    action: null,
  };
}

/** Evidence: whether the bytes the pipeline needs are complete. */
function evidenceLane(status: EvidenceStatus | null): LaneView {
  if (status === null) {
    return { id: "evidence", name: "Photo evidence", tone: "working", headline: "Waiting for the phone", detail: null, action: null };
  }
  if (status.complete_evidence) {
    return { id: "evidence", name: "Photo evidence", tone: "ok", headline: "Photos and poses complete", detail: null, action: null };
  }
  const kinds = [...status.missing_semantic_kinds];
  return {
    id: "evidence", name: "Photo evidence", tone: kinds.length > 0 ? "needs_action" : "working",
    headline: "Photo evidence incomplete",
    detail: kinds.length > 0 ? `Missing: ${joined(kinds)}` : joined(status.reasons),
    action: kinds.length > 0 ? null : { kind: "explain", label: "Why evidence is incomplete" },
  };
}

/** Recognition: the vision pass over the stored photos. Pending is a fact, never a promise. */
function recognitionLane(status: EvidenceStatus | null): LaneView {
  if (status === null) {
    return { id: "recognition", name: "Object recognition", tone: "working", headline: "Waiting for photos to arrive", detail: null, action: null };
  }
  const kind = status.semantic_state;
  if (kind === "complete") {
    return { id: "recognition", name: "Object recognition", tone: "ok", headline: "Photos checked for objects", detail: null, action: null };
  }
  if (kind === "failed") {
    return {
      id: "recognition", name: "Object recognition", tone: "failed",
      headline: "Object recognition did not finish",
      detail: "Nothing was reported. Ask it to try the stored photos again.",
      action: { kind: "retry", label: "Retry photo recognition" },
    };
  }
  const waiting: Record<string, LaneView> = {
    not_started: {
      id: "recognition", name: "Object recognition", tone: "needs_action",
      headline: "Not checked yet",
      detail: "Object recognition waits for complete photo evidence.",
      action: EXPLAIN,
    },
    blocked_incomplete_evidence: {
      id: "recognition", name: "Object recognition", tone: "needs_action",
      headline: "Waiting for more photos",
      detail: `Missing: ${joined(status.missing_semantic_kinds) || "complete evidence"}`,
      action: RECAPTURE,
    },
    settling: { id: "recognition", name: "Object recognition", tone: "working", headline: "Checking photos for objects", detail: null, action: null },
    queued: { id: "recognition", name: "Object recognition", tone: "working", headline: "Checking photos for objects", detail: null, action: null },
    running: { id: "recognition", name: "Object recognition", tone: "working", headline: "Checking photos for objects", detail: null, action: null },
  };
  return waiting[kind];
}

/** Visual: the photo-textured model, shown separately from measurements. */
function visualLane(status: TextureStatus | null): LaneView {
  if (status === null) return missingVisual();
  const view = textureStatusView(status);
  if (status.state === "complete") return completeVisual(view.message);
  if (view.working) return workingVisual(view.message);
  return stalledVisual(status.state, view);
}

function missingVisual(): LaneView {
  return {
    id: "visual", name: "Photo textures", tone: "needs_action",
    headline: "No photo-textured model yet",
    detail: "Measurements work without it; textures make the model look like the shop.",
    action: null,
  };
}

function completeVisual(message: string | null): LaneView {
  return { id: "visual", name: "Photo textures", tone: "ok", headline: message ?? "Photo textures ready", detail: null, action: null };
}

function workingVisual(message: string | null): LaneView {
  return { id: "visual", name: "Photo textures", tone: "working", headline: message ?? "Building photo textures", detail: null, action: null };
}

function stalledVisual(state: TextureStatus["state"], view: ReturnType<typeof textureStatusView>): LaneView {
  const tone = state === "failed" ? "failed" : "needs_action";
  const action: LaneAction | null = view.actionLabel ? { kind: "retry", label: view.actionLabel } : null;
  return {
    id: "visual", name: "Photo textures", tone,
    headline: view.message ?? "Photo textures not built",
    detail: "The measured plan and photos stay usable while textures are unavailable.",
    action,
  };
}

export type EvidenceLanes = { lanes: LaneView[]; allDone: boolean };

/** The four lanes, each with its own state, so no lane's pending hides the others' results. */
export function evidenceLanes(status: EvidenceStatus | null, textures: TextureStatus | null, measured: boolean): EvidenceLanes {
  const lanes = [
    geometryLane(status, measured),
    evidenceLane(status),
    recognitionLane(status),
    visualLane(textures),
  ];
  return { lanes, allDone: lanes.every((lane) => lane.tone === "ok") };
}

/** One visible note for each unanswered lane, for the top of the review panel. */
export function openEvidenceNotes(lanes: LaneView[]): string[] {
  return lanes
    .filter((lane) => lane.tone === "needs_action" || lane.tone === "failed")
    .map((lane) => (lane.detail ? `${lane.headline}: ${lane.detail}` : lane.headline));
}
