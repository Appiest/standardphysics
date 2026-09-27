import type { Checklist, ChecklistItem, Finding, Journey, OwnerRequest, Scenario, SceneGraph, SceneNode } from "@/types/contracts";

export type ChecklistStatus = ChecklistItem["status"];

/** Where else customers go, besides in, to the counter and out again. */
export type Destination = "seating" | "restroom" | "fitting_room" | "shelves" | "pickup";

/** The places to start the path with: the restroom when the owner said customers use one. */
export function defaultPlaces(requests: OwnerRequest[]): Destination[] {
  const restroom = requests.find((request) => request.id === "restroom");
  return restroom?.answer?.yes ? ["restroom"] : [];
}

/** Which part of the owner view the journey's next step needs on screen. */
export type Panel = "waiting" | "answers" | "counter" | "path" | "follow_ups" | "results" | "failed";

const PANEL_FOR_STEP: Record<Journey["next_step"]["kind"], Panel> = {
  upload: "waiting",
  measuring: "waiting",
  answers: "answers",
  photos: "answers",
  counter: "counter",
  path: "path",
  follow_ups: "follow_ups",
  results: "results",
  checklist: "results",
  done: "results",
  failed: "failed",
};

export function panelFor(journey: Journey): Panel {
  return PANEL_FOR_STEP[journey.next_step.kind];
}

/** Whether the view should poll the server, because the shop is still being uploaded, measured or checked. */
export function isWaiting(journey: Journey): boolean {
  return journey.next_step.kind === "upload" || journey.next_step.kind === "measuring";
}

/** The in-shop requests still to answer, in the order the app asks them. */
export function openInShop(requests: OwnerRequest[]): OwnerRequest[] {
  return requests.filter((request) => request.timing === "in_shop" && request.status === "open" && request.kind !== "another_look");
}

/**
 * The requests the next step names: the yes or no questions first, then the
 * photos, then the door push, the same order the app asks them in the shop.
 */
export function requestsForStep(journey: Journey, requests: OwnerRequest[]): OwnerRequest[] {
  const open = openInShop(requests);
  if (journey.next_step.kind === "photos") return open.filter((request) => request.kind === "photo");
  const questions = open.filter((request) => request.kind === "yes_no");
  return questions.length > 0 ? questions : open.filter((request) => request.kind === "number");
}

export function followUps(requests: OwnerRequest[]): OwnerRequest[] {
  return requests.filter((request) => request.timing === "follow_up" && request.status !== "not_applicable");
}

/** Problems in the order the checklist has them, each with its status. */
export function checklistRows(problems: Finding[], checklist: Checklist): { finding: Finding; status: ChecklistStatus }[] {
  const statuses = new Map(checklist.items.map((item) => [item.finding_id, item.status]));
  return problems.map((finding) => ({ finding, status: statuses.get(finding.id) ?? "to_do" }));
}

/** The owner started fixing once any item has a status, or once they said so. */
export function isFixing(checklist: Checklist, startedHere: boolean): boolean {
  return startedHere || checklist.done > 0;
}

export function thingsToFix(count: number): string {
  if (count === 0) return "Nothing to fix";
  return count === 1 ? "1 thing to fix" : `${count} things to fix`;
}

/**
 * How a measurement compares with the number the standard needs, drawn as two bars.
 * Both are scaled against the larger, so the longer bar always fills the track.
 */
export function comparisonBars(measured: number, required: number): { measured: number; required: number } {
  const longest = Math.max(measured, required, 1);
  return { measured: measured / longest, required: required / longest };
}

/**
 * The walk-through starts at the front door, facing the next stop on the path.
 * Positions are in the viewer's frame (x, height, -y), where rolling forward at
 * heading h moves along (-sin h, -cos h).
 */
export function wheelchairStartFrom(scenario: Scenario | null): { position: [number, number, number]; yaw: number } | null {
  const [door, next] = scenario?.stops ?? [];
  if (!door || !next) return null;
  const x = door.position.x;
  const z = -door.position.y;
  const yaw = Math.atan2(-(next.position.x - x), -(-next.position.y - z));
  return { position: [x, 0, z], yaw };
}

/** Checks a layout change can clear, because they're about where things stand. */
export const MOVABLE_CHECKS = new Set([
  "route_clear_width", "passing_space", "turning_space", "turn_clear_width", "exit_path",
  "service_counter_approach", "door_maneuvering_clearance",
]);

/** The first piece worth dragging: one that stands in a spot a problem is about, and can move. */
export function pieceToTry(problems: Finding[], scene: SceneGraph): SceneNode | null {
  const byId = new Map(scene.nodes.map((node) => [node.id, node]));
  for (const finding of problems.filter((problem) => MOVABLE_CHECKS.has(problem.check_id))) {
    const piece = finding.locus?.node_ids.map((id) => byId.get(id)).find((node) => node?.movable);
    if (piece) return piece;
  }
  return null;
}
