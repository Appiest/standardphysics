import type { TextureProgress, TextureStatus } from "@/types/contracts";

type State = TextureStatus["state"];

const REFRESH_STATES = new Set<State>(["queued", "running", "waiting_for_photos"]);

/** Whether the page should keep polling the server while photo textures are worked on. */
export function isTextureRefreshing(state: State): boolean {
  return REFRESH_STATES.has(state);
}

/** A status response is only safe to apply to the scan revision it was requested for. */
export function textureStatusMatches(status: TextureStatus, scanId: string, revision: number): boolean {
  return status.scan_id === scanId && status.revision === revision;
}

export type TextureStatusView = {
  message: string | null;
  working: boolean;
  actionLabel: string | null;
};

const BASE_VIEW: Record<State, Omit<TextureStatusView, "actionLabel">> = {
  needs_photos: { message: "New photo capture needed", working: false },
  waiting_for_photos: { message: "Photos are still uploading", working: true },
  not_started: { message: null, working: false },
  queued: { message: "Adding photo textures", working: true },
  running: { message: "Adding photo textures", working: true },
  complete: { message: null, working: false },
  failed: { message: "Textures didn't finish", working: false },
};

const ACTION_LABEL: Partial<Record<State, string>> = {
  not_started: "Add photo textures",
  failed: "Try textures again",
};

/** What to say about photo textures, and what button (if any) offers to change it. */
export function textureStatusView(status: TextureStatus): TextureStatusView {
  const base = BASE_VIEW[status.state];
  const canOfferRetry = status.state !== "failed" || status.can_retry;
  const actionLabel = canOfferRetry ? ACTION_LABEL[status.state] ?? null : null;
  const message = status.state === "complete" && status.build
    ? `Photos cover ${Math.round(status.build.coverage.textured_fraction * 100)}% of surfaces`
    : base.message;
  return { ...base, message, actionLabel };
}

/** How each step of a build is named to the person waiting on it. Steps not listed read as the build as a whole. */
const STEP_LABELS: Record<string, string> = {
  "box model": "Building the room",
  "choosing photos": "Choosing the best photos",
  "box layout": "Building the room",
  "box occlusion": "Checking what each photo shows",
  "scan occlusion": "Checking what each photo shows",
  "box exposure": "Matching photo brightness",
  "painted scan": "Painting the scan",
  "mesh load": "Loading the scan",
  "people removal": "Removing people",
  "object holes": "Filling holes in furniture",
  "seen-through buffers": "Checking what each photo shows",
  "mirrored completion": "Filling in the hidden sides of furniture",
  "hole patches": "Patching walls and floor",
  unwrap: "Preparing the surface",
  texels: "Preparing the surface",
  exposure: "Matching photo brightness",
  "photo bake": "Painting from the photos",
  "atlas image": "Saving the texture",
};

const WHOLE_BUILD_LABEL = "Adding photos";
const SECONDS_BEFORE_ESTIMATE = 5;

export type TextureProgressView = {
  label: string;
  /** How much of the step is done, from 0 to 1, when the step counts what it works through. */
  fraction: number | null;
  timeLeft: string | null;
};

/** The step a running build is on, how far through it, and how long it has left when that can be worked out. */
export function textureProgressView(progress: TextureProgress | null | undefined): TextureProgressView | null {
  if (!progress) return null;
  const fraction = progress.total ? Math.min(1, (progress.done ?? 0) / progress.total) : null;
  return { label: STEP_LABELS[progress.step] ?? WHOLE_BUILD_LABEL, fraction, timeLeft: timeLeft(progress, fraction) };
}

/** The rest of the step at the pace it has kept so far, measured on the build's own clock. */
function timeLeft(progress: TextureProgress, fraction: number | null): string | null {
  if (!fraction || fraction >= 1) return null;
  const elapsed = (Date.parse(progress.reported_at) - Date.parse(progress.step_started_at)) / 1000;
  if (!(elapsed >= SECONDS_BEFORE_ESTIMATE)) return null;
  return describeSeconds((elapsed * (1 - fraction)) / fraction);
}

function describeSeconds(seconds: number): string {
  if (seconds < 50) return "Less than a minute left";
  const minutes = Math.round(seconds / 60);
  return minutes === 1 ? "About a minute left" : `About ${minutes} minutes left`;
}
