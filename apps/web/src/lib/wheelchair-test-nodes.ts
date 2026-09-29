import type { SceneNode } from "@/types/contracts";

export function node({
  id,
  kind,
  dimensions,
  x = 0,
  y = 0,
  rotation = 0,
  parentId = null,
  transform,
}: {
  id: string;
  kind: string;
  dimensions: { x: number; y: number; z: number };
  x?: number;
  y?: number;
  rotation?: number;
  parentId?: string | null;
  transform?: SceneNode["transform"]["m"];
}): SceneNode {
  const radians = (rotation * Math.PI) / 180;
  const cosine = Math.cos(radians);
  const sine = Math.sin(radians);
  return {
    id,
    kind,
    label: id,
    labeled_by: "fixture",
    movable: false,
    parent_id: parentId,
    quality: "measured",
    raw_category: kind,
    dimensions,
    transform: { m: transform ?? [cosine, -sine, 0, x, sine, cosine, 0, y, 0, 0, 1, 0, 0, 0, 0, 1] },
  };
}
