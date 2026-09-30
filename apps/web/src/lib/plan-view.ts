import { footprint } from "@/components/FloorPlan";
import type { SceneGraph, SceneNode } from "@/types/contracts";
import { isListed } from "./found-objects";
import { restsOnSomething } from "./moves";

/**
 * The owner's plan is drawn in room metres with y flipped, so north is up, and
 * then turned as a whole so the room's walls run square with the screen. A
 * phone scan starts wherever the phone was pointing, which leaves most rooms
 * drawn at an angle otherwise.
 */

export type PlanPoint = { x: number; y: number };
export type PlanBox = { minX: number; minY: number; width: number; height: number };

const QUARTER_TURN_DEGREES = 90;

function radians(degrees: number): number {
  return (degrees * Math.PI) / 180;
}

/** Any angle folded into [-45, 45): walls at 10° and 100° run the same way. */
export function foldToQuarter(degrees: number): number {
  const folded = ((degrees % QUARTER_TURN_DEGREES) + QUARTER_TURN_DEGREES) % QUARTER_TURN_DEGREES;
  return folded >= QUARTER_TURN_DEGREES / 2 ? folded - QUARTER_TURN_DEGREES : folded;
}

/**
 * How far to turn the drawing so the walls run square with the screen. Each
 * wall votes for its direction by its length, on a circle where a quarter turn
 * comes back to the start, so long walls outweigh the short jogs around a door.
 */
export function planTurnDegrees(scene: SceneGraph): number {
  const walls = scene.nodes.filter((node) => node.kind === "wall" && node.dimensions.x > 0);
  const vote = walls.reduce((sum, wall) => {
    const angle = radians(footprint(wall).degrees * 4);
    return { x: sum.x + wall.dimensions.x * Math.cos(angle), y: sum.y + wall.dimensions.x * Math.sin(angle) };
  }, { x: 0, y: 0 });
  if (Math.hypot(vote.x, vote.y) < 1e-9) return 0;
  return foldToQuarter((Math.atan2(vote.y, vote.x) * 180) / Math.PI / 4);
}

/** A room point where it lands on the turned drawing. */
export function turnedPoint(room: PlanPoint, turnDegrees: number): PlanPoint {
  const [cos, sin] = [Math.cos(radians(turnDegrees)), Math.sin(radians(turnDegrees))];
  const [x, y] = [room.x, -room.y];
  return { x: cos * x - sin * y, y: sin * x + cos * y };
}

/** Half a footprint's reach across and down the turned drawing. */
export function turnedHalfExtent(node: SceneNode, turnDegrees: number): PlanPoint {
  const { width, depth, degrees } = footprint(node);
  const angle = radians(turnDegrees - degrees);
  const [cos, sin] = [Math.abs(Math.cos(angle)), Math.abs(Math.sin(angle))];
  return { x: (cos * width + sin * depth) / 2, y: (sin * width + cos * depth) / 2 };
}

/** The box the nodes fill once turned, with a margin all round. */
export function turnedBounds(nodes: SceneNode[], turnDegrees: number, marginShare = 0.05): PlanBox {
  if (nodes.length === 0) return { minX: -1, minY: -1, width: 2, height: 2 };
  const reach = nodes.map((node) => {
    const centre = turnedPoint(footprint(node), turnDegrees);
    const half = turnedHalfExtent(node, turnDegrees);
    return [centre.x - half.x, centre.y - half.y, centre.x + half.x, centre.y + half.y];
  });
  const [minX, minY] = [Math.min(...reach.map((box) => box[0])), Math.min(...reach.map((box) => box[1]))];
  const [maxX, maxY] = [Math.max(...reach.map((box) => box[2])), Math.max(...reach.map((box) => box[3]))];
  const margin = Math.max(maxX - minX, maxY - minY) * marginShare;
  return { minX: minX - margin, minY: minY - margin, width: maxX - minX + 2 * margin, height: maxY - minY + 2 * margin };
}

export function viewBoxAttribute(box: PlanBox): string {
  return `${box.minX} ${box.minY} ${box.width} ${box.height}`;
}

/** Plan text in metres, sized to the drawing so it lands near the same size on screen whatever the room's size. */
export function planTextMeters(box: PlanBox): number {
  return Math.max(box.width, box.height) / 28;
}

/** The affine part of a DOMMatrix: what `getScreenCTM().inverse()` hands back. */
export type Affine = { a: number; b: number; c: number; d: number; e: number; f: number };

/**
 * Where a pointer lands in room metres, given the inverse of the turned
 * drawing's screen matrix. The drawing flips y so north is up, so it flips back.
 */
export function roomPointFrom(inverse: Affine, client: { clientX: number; clientY: number }): PlanPoint {
  const x = inverse.a * client.clientX + inverse.c * client.clientY + inverse.e;
  const y = inverse.b * client.clientX + inverse.d * client.clientY + inverse.f;
  return { x, y: -y };
}

export type PlanRole = "movable" | "fixed" | "riding" | "backdrop";

/**
 * How a node takes part in the plan. Something resting up off the floor, a
 * laptop on a table or a drawer in a case, is drawn as part of what holds it
 * and never caught by the pointer, so it can't steal the grab from the case.
 */
export function planRole(node: SceneNode, floor: number): PlanRole {
  if (node.kind !== "object" || !isListed(node, floor)) return "backdrop";
  if (!node.movable) return "fixed";
  return restsOnSomething(node, floor) ? "riding" : "movable";
}

/** Floor under walls under furniture, then what can be picked up, then what rides on it. */
const LAYER: Record<string, number> = { floor: 0, object: 1, wall: 2, fixed: 3, movable: 4, riding: 5 };

function layerOf(node: SceneNode, floor: number): number {
  const role = planRole(node, floor);
  return LAYER[role === "backdrop" ? node.kind : role] ?? LAYER.object;
}

function footprintArea(node: SceneNode): number {
  return node.dimensions.x * node.dimensions.y;
}

/**
 * Nodes in the order they are drawn. The pointer catches whatever is drawn
 * last, so among pieces that can move the biggest goes last and wins the grab
 * where two overlap.
 */
export function drawOrder(nodes: SceneNode[], floor: number): SceneNode[] {
  return [...nodes].sort((a, b) => layerOf(a, floor) - layerOf(b, floor) || footprintArea(a) - footprintArea(b));
}

const MOVED_METERS = 0.01;
const TURNED_DEGREES = 0.5;

function angleBetween(a: number, b: number): number {
  return Math.abs(((((a - b) % 360) + 540) % 360) - 180);
}

/** True when the piece is somewhere other than where the scan found it, or turned. */
export function hasMoved(scanned: SceneNode, shown: SceneNode): boolean {
  const [from, to] = [footprint(scanned), footprint(shown)];
  return Math.hypot(to.x - from.x, to.y - from.y) > MOVED_METERS || angleBetween(to.degrees, from.degrees) > TURNED_DEGREES;
}

export type PlanLabel = { key: string; text: string; x: number; y: number };

/** Monospace figures run about 0.6 em wide; a little over keeps neighbours from touching. */
const CHARACTER_WIDTH_EM = 0.62;
const LINE_HEIGHT_EM = 1.25;
const MOST_NUDGES = 6;

function labelHalfWidth(text: string, size: number): number {
  return (text.length * CHARACTER_WIDTH_EM * size) / 2;
}

/**
 * Where a measurement's words go beside the stretch of line from `a` to `b`,
 * both already on the turned drawing: above a line that runs across, and to
 * the side facing `towardX` of one that runs up and down, so the words never
 * sit on the line they measure. The point is the text's baseline middle.
 */
export function labelBesideLine(a: PlanPoint, b: PlanPoint, text: string, size: number, towardX: number): PlanPoint {
  const middle = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  if (Math.abs(b.y - a.y) <= Math.abs(b.x - a.x)) return { x: middle.x, y: middle.y - size * 0.6 };
  const side = towardX >= middle.x ? 1 : -1;
  return { x: middle.x + side * (labelHalfWidth(text, size) + size * 0.5), y: middle.y + size * 0.35 };
}

function labelsOverlap(a: PlanLabel, b: PlanLabel, size: number): boolean {
  const reach = labelHalfWidth(a.text, size) + labelHalfWidth(b.text, size);
  return Math.abs(a.x - b.x) < reach && Math.abs(a.y - b.y) < LINE_HEIGHT_EM * size;
}

function isRepeat(label: PlanLabel, kept: PlanLabel[], size: number): boolean {
  return kept.some((other) => other.text === label.text && Math.hypot(other.x - label.x, other.y - label.y) < 2 * size);
}

/** 0, then one line up, one down, two up, two down, and so on. */
function nudgeLines(count: number): number[] {
  return Array.from({ length: count + 1 }, (_, index) => Math.ceil(index / 2) * (index % 2 === 1 ? -1 : 1));
}

function freeSpot(label: PlanLabel, placed: PlanLabel[], size: number): PlanLabel {
  for (const lines of nudgeLines(MOST_NUDGES * 2)) {
    const moved = { ...label, y: label.y + lines * LINE_HEIGHT_EM * size };
    if (!placed.some((other) => labelsOverlap(moved, other, size))) return moved;
  }
  return label;
}

/**
 * Measurement labels ready to draw: one of each reading where two findings
 * measured the same spot, and the rest slid up or down a line until none sit
 * on top of another.
 */
export function placeLabels(labels: PlanLabel[], size: number): PlanLabel[] {
  const kept: PlanLabel[] = [];
  for (const label of labels) {
    if (!isRepeat(label, kept, size)) kept.push(label);
  }
  return kept.reduce<PlanLabel[]>((placed, label) => [...placed, freeSpot(label, placed, size)], []);
}
