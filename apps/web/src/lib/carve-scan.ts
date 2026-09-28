import { Box3, BufferAttribute, BufferGeometry, Matrix4, Vector3 } from "three";
import { toViewerMatrix } from "@/lib/scene-matrix";
import type { SceneNode } from "@/types/contracts";

/** How far past a measured box the scanned surface of a piece still reaches; the boxes miss real surfaces by inches. */
const REACH = 0.06;
/** Surface this close above a piece's underside is the floor it stands on, which stays behind when the piece moves. */
const FLOOR_SKIN = 0.04;

/** A piece's measured box in viewer space, grown by REACH, with the floor under it left out. */
export type CarveRegion = { id: string; toLocal: Matrix4; half: Vector3; bottom: number; bounds: Box3 };

export function carveRegion(node: SceneNode): CarveRegion {
  const toWorld = toViewerMatrix(node.transform);
  const half = new Vector3(node.dimensions.x / 2 + REACH, node.dimensions.z / 2 + REACH, node.dimensions.y / 2 + REACH);
  const bounds = new Box3(half.clone().negate(), half.clone()).applyMatrix4(toWorld);
  return { id: node.id, toLocal: toWorld.clone().invert(), half, bottom: -node.dimensions.z / 2 + FLOOR_SKIN, bounds };
}

const LOCAL = new Vector3();

function holds(region: CarveRegion, point: Vector3): boolean {
  if (!region.bounds.containsPoint(point)) return false;
  LOCAL.copy(point).applyMatrix4(region.toLocal);
  const { half } = region;
  return Math.abs(LOCAL.x) <= half.x && Math.abs(LOCAL.z) <= half.z && LOCAL.y <= half.y && LOCAL.y >= region.bottom;
}

/** Which region each triangle's centre falls in, or -1 for the room around them. */
function ownersOf(index: ArrayLike<number>, position: BufferAttribute, toWorld: Matrix4, regions: CarveRegion[]): Int32Array {
  const owners = new Int32Array(index.length / 3).fill(-1);
  const [a, b, c] = [new Vector3(), new Vector3(), new Vector3()];
  for (let triangle = 0; triangle < owners.length; triangle++) {
    a.fromBufferAttribute(position, index[triangle * 3]);
    b.fromBufferAttribute(position, index[triangle * 3 + 1]);
    c.fromBufferAttribute(position, index[triangle * 3 + 2]);
    const centre = a.add(b).add(c).divideScalar(3).applyMatrix4(toWorld);
    owners[triangle] = regions.findIndex((region) => holds(region, centre));
  }
  return owners;
}

function indexOf(geometry: BufferGeometry): ArrayLike<number> {
  if (geometry.index) return geometry.index.array;
  return Uint32Array.from({ length: geometry.getAttribute("position").count }, (_, vertex) => vertex);
}

type Group = { start: number; count: number; materialIndex?: number };

/** Appends the corners of this group's triangles that belong to `owner`. */
function keepOwned(kept: number[], index: ArrayLike<number>, owners: Int32Array, owner: number, group: Group) {
  for (let corner = group.start; corner < group.start + group.count; corner += 3) {
    if (owners[corner / 3] === owner) kept.push(index[corner], index[corner + 1], index[corner + 2]);
  }
}

/** A geometry holding only the given triangles, sharing the source's vertices and keeping its material groups. */
function subset(source: BufferGeometry, index: ArrayLike<number>, owners: Int32Array, owner: number): BufferGeometry | null {
  const grouped = source.groups.length > 0;
  const groups: Group[] = grouped ? source.groups : [{ start: 0, count: index.length }];
  const kept: number[] = [];
  const out = new BufferGeometry();
  for (const group of groups) {
    const start = kept.length;
    keepOwned(kept, index, owners, owner, group);
    if (grouped && kept.length > start) out.addGroup(start, kept.length - start, group.materialIndex);
  }
  if (kept.length === 0) return null;
  for (const [name, attribute] of Object.entries(source.attributes)) out.setAttribute(name, attribute);
  out.setIndex(kept);
  return out;
}

/**
 * Splits one scanned mesh into the room and the pieces standing in it.
 *
 * Every triangle whose centre sits inside a piece's measured box goes with
 * that piece; the rest, the floor under it included, stays with the room. The
 * pieces keep the scan's own vertices, so a moved counter is the counter the
 * phone photographed, not a box standing in for it.
 */
export function carveGeometry(geometry: BufferGeometry, toWorld: Matrix4, regions: CarveRegion[]) {
  const index = indexOf(geometry);
  const owners = ownersOf(index, geometry.getAttribute("position") as BufferAttribute, toWorld, regions);
  const pieces = new Map<string, BufferGeometry>();
  regions.forEach((region, owner) => {
    const piece = subset(geometry, index, owners, owner);
    if (piece) pieces.set(region.id, piece);
  });
  return { room: subset(geometry, index, owners, -1), pieces };
}
