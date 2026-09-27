import type { Finding, SceneNode } from "@/types/contracts";

export const MODEL = {
  ground: "#efece5",
  floor: "#f4f2ec",
  wall: "#8e897f",
  fixture: "#e2ded5",
  movable: "#fbfaf7",
  opening: "#dbe4ef",
  ink: "#1b1c1e",
  problem: "#c8372d",
  pass: "#2e7d4f",
  accent: "#2f5e9e",
  outlet: "#e69f00",
  candidate_outlet: "#d55e00",
} as const;

export const WALL_CUT_HEIGHT = 1.2;

/**
 * Where the scanned surface is cut in the overview, in metres.
 *
 * Higher than the boxes' cut, because the scan is worth looking at for the
 * shelving and signage the photos show, and lower than a ceiling, so the lid
 * closing the ceiling's holes never roofs over the room seen from above.
 */
export const SCAN_CUT_HEIGHT = 2.2;

const NODE_COLORS: Record<string, string> = {
  outlet: MODEL.outlet,
  candidate_outlet: MODEL.candidate_outlet,
  wall: MODEL.wall,
  floor: MODEL.floor,
};

export function nodeColor(node: SceneNode): string {
  if (node.appearance?.base_color) return node.appearance.base_color;
  if (node.kind === "object") return node.movable ? MODEL.movable : MODEL.fixture;
  return NODE_COLORS[node.kind] ?? MODEL.opening;
}

export function outcomeColor(outcome: Finding["outcome"]): string {
  if (outcome === "problem") return MODEL.problem;
  if (outcome === "passes") return MODEL.pass;
  return MODEL.ink;
}
