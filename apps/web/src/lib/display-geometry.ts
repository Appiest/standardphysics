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

/** `occupancy.UNCLAIMED_SURFACE`: a box the scan drew round LiDAR faces nothing claimed, which marks where a surface was seen. */
const UNCLAIMED_SURFACE = "lidar_candidate";
/** `bounds_the_room` in the contracts: how thin, and how broad or long, a region is to read as a sheet of the room. */
const SHEET_THICKNESS = 0.05;
const SHEET_AREA = 1.0;
const SHEET_REACH = 2.0;

function uprightExtent(node: SceneNode): number {
  const m = node.transform.m;
  const { x, y, z } = node.dimensions;
  return Math.abs(m[8]) * x + Math.abs(m[9]) * y + Math.abs(m[10]) * z;
}

function boundsTheRoom(node: SceneNode): boolean {
  const [thinnest, middle, longest] = [node.dimensions.x, node.dimensions.y, node.dimensions.z].sort((a, b) => a - b);
  if (thinnest > SHEET_THICKNESS || middle <= SHEET_THICKNESS) return false;
  return middle * longest >= SHEET_AREA || longest >= SHEET_REACH;
}

function wallMiddle(nodes: SceneNode[]): number | null {
  const upright = nodes.filter((node) => boundsTheRoom(node) && uprightExtent(node) > SHEET_THICKNESS);
  return upright.length ? Math.max(...upright.map((node) => node.transform.m[11])) : null;
}

function isALid(node: SceneNode, lidHeight: number | null): boolean {
  const flat = boundsTheRoom(node) && uprightExtent(node) <= SHEET_THICKNESS;
  return lidHeight !== null && flat && node.transform.m[11] - uprightExtent(node) / 2 > lidHeight;
}

/**
 * The regions the model draws, as `blender.display_graph` exports them: none of the boxes round unclaimed
 * LiDAR, which on a real scan span the whole room, and no flat sheet above the middle of the walls, which
 * roofs the room over for a camera looking in from above.
 */
export function drawnInModel(nodes: SceneNode[]): SceneNode[] {
  const lidHeight = wallMiddle(nodes);
  return nodes.filter((node) => node.raw_category !== UNCLAIMED_SURFACE && !isALid(node, lidHeight));
}
