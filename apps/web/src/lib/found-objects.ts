import type { SceneGraph, SceneNode, Vec3 } from "@/types/contracts";
import { formatInches } from "./findings";
import { metersToInches } from "./units";

export type FoundGroupId = "service" | "seating" | "safety" | "other";

/** Every piece of one kind in one group, such as the shop's 23 chairs. */
export type FoundRow = {
  id: string;
  group: FoundGroupId;
  name: string;
  nodeIds: string[];
  /** Top heights in inches, lowest first; empty for kinds whose height the ADA checks don't use. */
  topInches: number[];
};

export type FoundGroup = { id: FoundGroupId; title: string; rows: FoundRow[] };

/** A piece's box in inches: across, front to back, and floor to top of the box. */
export type PieceSize = { wideInches: number; deepInches: number; tallInches: number };

/** One found piece as the 3D view draws it. */
export type FoundMark = { nodeId: string; rowId: string; name: string; topInches: number | null; size: PieceSize };

const GROUP_ORDER: FoundGroupId[] = ["service", "seating", "safety", "other"];

const GROUP_TITLES: Record<FoundGroupId, string> = {
  service: "Counters and payment",
  seating: "Tables and seating",
  safety: "Safety",
  other: "Everything else",
};

/** Checked in order, so an extinguisher cabinet on a counter still reads as safety. */
const GROUP_WORDS: [FoundGroupId, string[]][] = [
  ["safety", ["fire extinguisher", "extinguisher", "first aid", "defibrillator", "aed", "exit sign", "fire alarm"]],
  ["service", ["counter", "register", "payment terminal", "card terminal", "card reader", "kiosk", "order sign", "menu board"]],
  ["seating", ["chair", "bench", "stool", "table", "sofa", "couch", "booth", "seat"]],
];

/** Kinds the ADA checks measure by height: counter tops, reach to a terminal, table surfaces. */
const HEIGHT_WORDS = ["counter", "register", "payment terminal", "card terminal", "card reader", "kiosk", "table", "extinguisher", "first aid", "dispenser"];

/** Things set down on a counter for the day, which the owner never needs listed. */
const LOOSE_ITEMS = ["cup", "cups", "bag", "box", "drip tray", "metal container"];

const FLOOR_STANDING = ["chair", "bench", "stool", "table", "sofa", "couch", "booth", "counter"];

const SMALLEST_PIECE_METERS = 0.15;
const LOWEST_FURNITURE_METERS = 0.3;
const HIGHEST_FURNITURE_FOOT_METERS = 0.3;

/** "Wall_shelf" and "wall shelf" read the same. */
export function normalName(text: string): string {
  return text.replace(/_/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
}

function mentions(text: string, phrase: string): boolean {
  return ` ${text} `.includes(` ${phrase} `);
}

function mentionsAny(text: string, phrases: string[]): boolean {
  return phrases.some((phrase) => mentions(text, phrase));
}

function sentenceCase(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function describedBy(node: SceneNode): string {
  return `${normalName(node.label)} ${normalName(node.raw_category)}`;
}

export function groupOf(node: SceneNode): FoundGroupId {
  const described = describedBy(node);
  return GROUP_WORDS.find(([, words]) => mentionsAny(described, words))?.[0] ?? "other";
}

/** Half the box's extent along the room's up axis, whichever way the box is turned. */
function halfHeight(node: SceneNode): number {
  const m = node.transform.m;
  const { x, y, z } = node.dimensions;
  return 0.5 * (Math.abs(m[8]) * x + Math.abs(m[9]) * y + Math.abs(m[10]) * z);
}

export function floorHeight(scene: SceneGraph): number {
  const floors = scene.nodes.filter((node) => node.kind === "floor").map((node) => node.transform.m[11]);
  return floors.length > 0 ? Math.min(...floors) : 0;
}

/** The box's highest point in room coordinates. */
export function boxTop(node: SceneNode): number {
  return node.transform.m[11] + halfHeight(node);
}

export function topMeters(node: SceneNode, floor: number): number {
  return boxTop(node) - floor;
}

function bottomMeters(node: SceneNode, floor: number): number {
  return node.transform.m[11] - halfHeight(node) - floor;
}

/** A chair a hand tall, or one hanging half a metre up, is a misread photo rather than furniture. */
function isImplausibleFurniture(node: SceneNode, floor: number): boolean {
  if (!mentionsAny(describedBy(node), FLOOR_STANDING)) return false;
  return node.dimensions.z < LOWEST_FURNITURE_METERS || bottomMeters(node, floor) > HIGHEST_FURNITURE_FOOT_METERS;
}

/** Structure, loose items and specks: the pieces an owner doesn't need listed. */
export function isListed(node: SceneNode, floor: number): boolean {
  if (node.kind !== "object") return false;
  if (Math.max(node.dimensions.x, node.dimensions.y, node.dimensions.z) < SMALLEST_PIECE_METERS) return false;
  if (mentionsAny(describedBy(node), LOOSE_ITEMS)) return false;
  return !isImplausibleFurniture(node, floor);
}

function heightMatters(node: SceneNode): boolean {
  return mentionsAny(describedBy(node), HEIGHT_WORDS);
}

export function displayName(node: SceneNode): string {
  return sentenceCase(normalName(node.label));
}

function rowOf(rows: Map<string, FoundRow>, node: SceneNode): FoundRow {
  const group = groupOf(node);
  const name = displayName(node);
  const id = `${group}:${name}`;
  const existing = rows.get(id);
  if (existing) return existing;
  const row: FoundRow = { id, group, name, nodeIds: [], topInches: [] };
  rows.set(id, row);
  return row;
}

function byCountThenName(a: FoundRow, b: FoundRow): number {
  return b.nodeIds.length - a.nodeIds.length || a.name.localeCompare(b.name);
}

/** What the scan found, grouped for an owner and with structure and noise left out. */
export function foundGroups(scene: SceneGraph): FoundGroup[] {
  const floor = floorHeight(scene);
  const rows = new Map<string, FoundRow>();
  for (const node of scene.nodes.filter((candidate) => isListed(candidate, floor))) {
    const row = rowOf(rows, node);
    row.nodeIds.push(node.id);
    if (heightMatters(node)) row.topInches.push(metersToInches(topMeters(node, floor)));
  }
  for (const row of rows.values()) row.topInches.sort((a, b) => a - b);
  return GROUP_ORDER
    .map((id) => ({ id, title: GROUP_TITLES[id], rows: [...rows.values()].filter((row) => row.group === id).sort(byCountThenName) }))
    .filter((group) => group.rows.length > 0);
}

function pieceSize(node: SceneNode): PieceSize {
  return {
    wideInches: metersToInches(node.dimensions.x),
    deepInches: metersToInches(node.dimensions.y),
    tallInches: metersToInches(2 * halfHeight(node)),
  };
}

/** One mark per listed piece, each with its own height, for the 3D view. */
export function foundMarks(scene: SceneGraph, groups: FoundGroup[]): FoundMark[] {
  const floor = floorHeight(scene);
  const byId = new Map(scene.nodes.map((node) => [node.id, node]));
  return groups.flatMap((group) => group.rows.flatMap((row) => row.nodeIds.flatMap((nodeId) => {
    const node = byId.get(nodeId);
    if (!node) return [];
    const topInches = row.topInches.length > 0 ? metersToInches(topMeters(node, floor)) : null;
    return [{ nodeId, rowId: row.id, name: row.name, topInches, size: pieceSize(node) }];
  })));
}

function plural(name: string): string {
  if (/(s|x|ch|sh)$/.test(name)) return `${name}es`;
  if (/[^aeiou]y$/.test(name)) return `${name.slice(0, -1)}ies`;
  return `${name}s`;
}

/** "Counter", or "23 chairs" when there are several. */
export function rowLabel(row: FoundRow): string {
  const count = row.nodeIds.length;
  return count === 1 ? row.name : `${count} ${plural(row.name.toLowerCase())}`;
}

function kindName(row: FoundRow): string {
  const name = row.name.toLowerCase();
  return row.nodeIds.length === 1 ? name : plural(name);
}

/** "signs, screens and 13 more": what a folded list holds, named by its two biggest kinds. */
export function foldedSummary(rows: FoundRow[]): string {
  const [first, second] = rows.map(kindName);
  if (rows.length === 1) return first;
  if (rows.length === 2) return `${first} and ${second}`;
  return `${first}, ${second} and ${rows.length - 2} more`;
}

function inchesNumber(inches: number): string {
  return formatInches(inches).replace(/ in$/, "");
}

/** "35.7 in", or "29–43 in" when the pieces differ. */
export function heightRange(topInches: number[]): string | null {
  if (topInches.length === 0) return null;
  const low = inchesNumber(topInches[0]);
  const high = inchesNumber(topInches[topInches.length - 1]);
  return low === high ? `${low} in` : `${low}–${high} in`;
}

/** The middle of a row's pieces on the floor plan, at their average height, for the camera to face. */
export function rowCenter(scene: SceneGraph, row: FoundRow): Vec3 | null {
  const members = scene.nodes.filter((node) => row.nodeIds.includes(node.id));
  if (members.length === 0) return null;
  const mean = (pick: (node: SceneNode) => number) => members.reduce((sum, node) => sum + pick(node), 0) / members.length;
  return { x: mean((node) => node.transform.m[3]), y: mean((node) => node.transform.m[7]), z: mean((node) => node.transform.m[11]) };
}
