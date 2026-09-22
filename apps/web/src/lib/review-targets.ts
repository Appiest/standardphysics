import { isMarkedCounter } from "@/lib/counter";
import { isPlausibleSensorBox } from "./review-client";
import type { ObservationCrop, SceneGraph, SceneNode, UnlocalizedObservation, Vec3 } from "@/types/contracts";

export type TargetClass = "outlet" | "television" | "service_counter" | "restroom_entrance";

export const TARGET_CLASSES: TargetClass[] = ["outlet", "television", "service_counter", "restroom_entrance"];

export const TARGET_CLASS_LABEL: Record<TargetClass, string> = {
  outlet: "Outlets",
  television: "Televisions",
  service_counter: "Service counter",
  restroom_entrance: "Restroom entrance",
};

export type TargetClassFilter = TargetClass | "all";

/** The approximate spot a measured node stands at, from its stored transform.
 *
 * `Mat4.m` is row-major, so the translation lives at indices 3, 7 and 11 —
 * the same entries ShopModel and the camera bounds read. Reading 12..14
 * picked up the bottom row instead and reported every object near the origin.
 */
export function nodePosition(node: SceneNode): Vec3 | null {
  const m = node.transform?.m;
  if (!m || m.length < 16 || !m.every((value) => Number.isFinite(value))) return null;
  return { x: m[3], y: m[7], z: m[11] };
}

const WALL_KINDS = new Set(["wall", "floor", "ceiling", "opening", "door", "window", "room"]);

function isWhiteboardLike(node: SceneNode): boolean {
  return node.kind === "whiteboard" || node.raw_category === "whiteboard";
}

function carriesAttachmentEvidence(node: SceneNode): boolean {
  if (node.attachment === null || node.attachment === undefined) return false;
  if (node.raw_category !== "outlet" && node.attachment.review_status === undefined) return false;
  return !WALL_KINDS.has(node.kind) && node.kind !== "object";
}

/** An outlet the pipeline photographed, exactly as the old outlet panel matched it. */
export function isOutletNode(node: SceneNode): boolean {
  if (isWhiteboardLike(node)) return false;
  if (node.kind === "outlet" || node.kind === "candidate_outlet") return true;
  return carriesAttachmentEvidence(node);
}

function mentions(...texts: (string | null | undefined)[]): boolean {
  return texts.some((text) => text?.toLowerCase().includes("restroom") || text?.toLowerCase().includes("bathroom"));
}

function televisionText(text: string): boolean {
  const lower = text.toLowerCase();
  return lower === "television" || lower === "tv" || lower === "televisions" || lower === "tv screen";
}

/** Which target class a measured node is evidence for, if any. Null means "not a target". */
export function nodeTargetClass(node: SceneNode): TargetClass | null {
  if (node.kind === "television" || node.kind === "candidate_television") return "television";
  if (televisionText(node.raw_category) || televisionText(node.label)) return "television";
  if (mentions(node.kind, node.raw_category, node.label)) return "restroom_entrance";
  if (isMarkedCounter(node)) return "service_counter";
  if (isOutletNode(node)) return "outlet";
  return null;
}

export type ReviewEntry =
  | { source: "node"; node: SceneNode; observations: ObservationCrop[]; targetClass: TargetClass }
  | { source: "unlocalized"; observation: UnlocalizedObservation; targetClass: TargetClass };

const UNLOCALIZED_CLASS: Record<string, TargetClass> = {
  outlet: "outlet",
  television: "television",
  service_counter: "service_counter",
  restroom_entrance: "restroom_entrance",
};

function matchesFilter(filter: TargetClassFilter, targetClass: TargetClass): boolean {
  return filter === "all" || filter === targetClass;
}

function nodeEntriesFor(scene: SceneGraph, filter: TargetClassFilter): ReviewEntry[] {
  const entries: ReviewEntry[] = [];
  for (const node of scene.nodes) {
    const targetClass = nodeTargetClass(node);
    if (targetClass === null || !matchesFilter(filter, targetClass)) continue;
    entries.push({ source: "node", node, observations: node.attachment?.observations ?? [], targetClass });
  }
  return entries;
}

function unlocalizedEntriesFor(scene: SceneGraph, filter: TargetClassFilter): ReviewEntry[] {
  const entries: ReviewEntry[] = [];
  for (const observation of scene.unlocalized_observations ?? []) {
    const targetClass = UNLOCALIZED_CLASS[observation.target_class];
    if (targetClass === undefined || !matchesFilter(filter, targetClass)) continue;
    entries.push({ source: "unlocalized", observation, targetClass });
  }
  return entries;
}

/** Every piece of photo evidence for a class, both measured nodes and unlocalized marks. */
export function reviewEntriesFor(scene: SceneGraph, filter: TargetClassFilter): ReviewEntry[] {
  return [...nodeEntriesFor(scene, filter), ...unlocalizedEntriesFor(scene, filter)];
}

/** Photographed evidence of a class exists but only in photos, with no measured position. */
export function unlocalizedCount(scene: SceneGraph, targetClass: TargetClass): number {
  return (scene.unlocalized_observations ?? []).filter((observation) => UNLOCALIZED_CLASS[observation.target_class] === targetClass).length;
}

/**
 * The observation whose crop image can actually be shown. A manual mark's
 * server-cut crop counts exactly like an automatic one: both are authenticated
 * pixels from the frame, distinguished only by provenance.
 */
export function firstRenderableCrop(observations: ObservationCrop[]): ObservationCrop | null {
  return (
    observations.find((candidate) => candidate.image_url !== null && candidate.image_url !== undefined && isPlausibleSensorBox(candidate.sensor_box))
    ?? null
  );
}

/** No detection is `unknown`, never "none in the room". This is the wording to prove it. */
export function classEmptyDetail(targetClass: TargetClass): string {
  const label = TARGET_CLASS_LABEL[targetClass].toLowerCase().replace("entrance", "entrances");
  return `No ${label} were found in the photographed evidence. That means this scan does not know, not that the room is missing them. Mark one in a photo below if you can see it.`;
}
