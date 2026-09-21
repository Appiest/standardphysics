import { drawnNodes, footprint, planBounds, type PlanBounds } from "@/components/FloorPlan";
import type { SceneGraph } from "@/types/contracts";
import { formatFeetAndInches } from "./units";

export interface PlanNote {
  x: number;
  y: number;
  text: string;
}

export interface PlanAnnotations {
  bounds: PlanBounds;
  widthLabel: string;
  depthLabel: string;
  notes: PlanNote[];
}

function sentenceCase(label: string) {
  const trimmed = label.trim().replaceAll("_", " ");
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
}

export function planAnnotations(scene: SceneGraph, noteCount = 3): PlanAnnotations | null {
  const drawn = drawnNodes(scene);
  if (drawn.length === 0) return null;
  const bounds = planBounds(drawn);
  const notes = drawn
    .filter((node) => node.kind === "object" && node.label.trim() !== "")
    .sort((a, b) => b.dimensions.x * b.dimensions.y - a.dimensions.x * a.dimensions.y)
    .slice(0, noteCount)
    .map((node) => {
      const { x, y, width, depth } = footprint(node);
      return { x, y, text: `${sentenceCase(node.label)} ${formatFeetAndInches(width)} × ${formatFeetAndInches(depth)}` };
    });
  return {
    bounds,
    widthLabel: formatFeetAndInches(bounds.width),
    depthLabel: formatFeetAndInches(bounds.height),
    notes,
  };
}
