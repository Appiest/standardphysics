import type { Mat4, SceneGraph, SceneNode } from "@/types/contracts";

/** One room of a combined scan: a name and the nodes that belong to it. */
export type RoomGroup = {
  name: string;
  node_ids: string[];
};

/** Where the owner has dragged one room: rotate about its centroid, then slide. */
export type RoomPlacement = {
  yawDegrees: number;
  tx: number;
  ty: number;
  cx: number;
  cy: number;
};

export const IDENTITY_PLACEMENT: RoomPlacement = { yawDegrees: 0, tx: 0, ty: 0, cx: 0, cy: 0 };

/** The contract transform is row-major with translation at indices 3, 7, 11. */
function compose(m: number[], placement: RoomPlacement): number[] {
  const yaw = (placement.yawDegrees * Math.PI) / 180;
  const angle = Math.atan2(m[4], m[0]) + yaw;
  const cosAngle = Math.cos(angle);
  const sinAngle = Math.sin(angle);
  const px = m[3] - placement.cx;
  const py = m[7] - placement.cy;
  const rx = px * Math.cos(yaw) - py * Math.sin(yaw);
  const ry = px * Math.sin(yaw) + py * Math.cos(yaw);
  return [
    cosAngle, -sinAngle, 0, placement.cx + rx + placement.tx,
    sinAngle, cosAngle, 0, placement.cy + ry + placement.ty,
    0, 0, 1, m[11],
    0, 0, 0, 1,
  ];
}

/** The floor position (x, y) of a node, read from its transform. */
export function nodePosition(transform: Mat4): [number, number] {
  return [transform.m[3], transform.m[7]];
}

/** The room's centroid, over the nodes the manifest lists. */
export function centroid(nodes: SceneNode[]): [number, number] {
  let x = 0;
  let y = 0;
  for (const node of nodes) {
    x += node.transform.m[3];
    y += node.transform.m[7];
  }
  const count = nodes.length || 1;
  return [x / count, y / count];
}

export function moveNode(node: SceneNode, placement: RoomPlacement): SceneNode {
  return { ...node, transform: { m: compose(node.transform.m, placement) } as Mat4 };
}

/** The scene with every room placed where the owner dragged it. */
export function applyRoomPlacements(scene: SceneGraph, rooms: RoomGroup[], placements: Record<string, RoomPlacement>): SceneGraph {
  const roomByNode = new Map<string, string>();
  for (const room of rooms) {
    for (const nodeId of room.node_ids) roomByNode.set(nodeId, room.name);
  }
  const nodes = scene.nodes.map((node) => {
    const room = roomByNode.get(node.id);
    const placement = room ? placements[room] : undefined;
    return placement && placement !== IDENTITY_PLACEMENT ? moveNode(node, placement) : node;
  });
  return { ...scene, nodes };
}
