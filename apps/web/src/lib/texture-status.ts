import type { TextureStatus } from "@/types/contracts";

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
