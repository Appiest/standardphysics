/** Who made the pending moves: the model's suggestion, or the owner's own hand. */
export type MovesSource = "suggestion" | "owner" | null;

export type ArrangementEvent = "suggested" | "loaded" | "moved" | "cleared";

const SOURCE_AFTER: Record<ArrangementEvent, MovesSource> = {
  suggested: "suggestion",
  loaded: "owner",
  moved: "owner",
  cleared: null,
};

/** A drag or nudge on top of a suggestion makes the layout the owner's. */
export function sourceAfter(event: ArrangementEvent): MovesSource {
  return SOURCE_AFTER[event];
}

export function pendingLabels(source: MovesSource): { beforeLabel: string; afterLabel: string } {
  return { beforeLabel: "Now", afterLabel: source === "suggestion" ? "Suggested" : "With your moves" };
}
