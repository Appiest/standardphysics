import { Matrix4, Vector3, type BufferGeometry } from "three";
import type { SceneNode } from "@/types/contracts";

export const MIN_DISPLAY_WALL_THICKNESS = 0.08;
export const MAX_DISPLAY_WALL_HEIGHT = 2.8;
const SINGULAR_SCALE = 0.000001;

/** Rendering keeps a visible wall shell without changing the measured graph. */
export function displayScale(node: SceneNode): [number, number, number] {
  const depth = node.kind === "wall" ? Math.max(node.dimensions.y, MIN_DISPLAY_WALL_THICKNESS) : node.dimensions.y;
  const height = node.kind === "wall" ? Math.min(node.dimensions.z, MAX_DISPLAY_WALL_HEIGHT) : node.dimensions.z;
  return [node.dimensions.x, height, depth];
}

const SCRATCH_SIZE = new Vector3();

/** Cached GLBs made with a zero scale cannot be repaired by a transform alone. */
export function needsDisplayBoxFallback(node: SceneNode, geometry: BufferGeometry, matrix: Matrix4): boolean {
  if (node.kind !== "wall") return false;
  if (!geometry.boundingBox) geometry.computeBoundingBox();
  const size = geometry.boundingBox?.getSize(SCRATCH_SIZE);
  if (!size || Math.min(size.x, size.y, size.z) <= SINGULAR_SCALE) return true;
  const elements = matrix.elements;
  const axisLengths = [
    Math.hypot(elements[0], elements[1], elements[2]),
    Math.hypot(elements[4], elements[5], elements[6]),
    Math.hypot(elements[8], elements[9], elements[10]),
  ];
  return Math.min(...axisLengths) <= SINGULAR_SCALE;
}

/** A floor may be geometrically flat, but its placement transform must still be usable. */
export function hasUsableFloorMesh(geometry: BufferGeometry, matrix: Matrix4): boolean {
  if ((geometry.getAttribute("position")?.count ?? 0) < 3) return false;
  const elements = matrix.elements;
  if (!elements.every(Number.isFinite)) return false;
  const axisLengths = [
    Math.hypot(elements[0], elements[1], elements[2]),
    Math.hypot(elements[4], elements[5], elements[6]),
    Math.hypot(elements[8], elements[9], elements[10]),
  ];
  return Math.min(...axisLengths) > SINGULAR_SCALE;
}

/** Stale builds never supply shape geometry; only their unchanged nodes may use the baked GLB. */
export function canUseCapturedGlbGeometry(node: SceneNode, geometry: BufferGeometry, matrix: Matrix4, stale: boolean): boolean {
  if (stale) return false;
  return node.kind === "floor" ? hasUsableFloorMesh(geometry, matrix) : !needsDisplayBoxFallback(node, geometry, matrix);
}
