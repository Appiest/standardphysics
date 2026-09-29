export type FurnitureState =
  | "waiting_for_textures" | "not_applicable" | "unavailable" | "not_started" | "queued" | "running" | "done" | "failed";

export type FurnitureRefinement = {
  state: FurnitureState;
  build_id: string | null;
  error?: string | null;
  report?: { accepted: number } | null;
};

/** What the viewer dock shows for each state. A server without SPAR3D answers "unavailable", which shows nothing and stops the polling. */
const PROGRESS: Record<FurnitureState, "hidden" | "working" | "failed" | "result"> = {
  waiting_for_textures: "hidden",
  not_applicable: "hidden",
  unavailable: "hidden",
  not_started: "working",
  queued: "working",
  running: "working",
  failed: "failed",
  done: "result",
};

export function furnitureProgress(state: FurnitureState) {
  return PROGRESS[state] ?? "hidden";
}
