import type { Mat4, SceneGraph, SceneNode } from "@/types/contracts";

/** One room of a combined scan: a name and the nodes that belong to it. */
export type RoomGroup = {
  name: string;
  node_ids: string[];
  /** The scan this walk was uploaded as, whose photographed mesh stands in for its boxes. */
  source_scan_id?: string | null;
  /** That scan's captured surface, resolved on the server so the browser can load it. */
  scan_glb_url?: string | null;
  /** Where that scan's own frame stands in the revision shown, or null when it cannot be fitted. */
  capture_pose?: CapturePose | null;
};

/** A turn about the vertical, then a slide: new = R(yaw) p + t, in the room frame. */
export type CapturePose = { yaw_degrees: number; tx: number; ty: number };

const UNMOVED_CAPTURE: CapturePose = { yaw_degrees: 0, tx: 0, ty: 0 };

/**
 * Where to put a walk's captured mesh so it sits where its boxes sit.
 *
 * The mesh is still in the frame its phone measured it in, which is not where
 * its boxes are: the merge spread the walks apart and every save placed them
 * again. `capture` carries it from that frame to the revision shown, and the
 * drag in progress goes on top, rotating about the same centroid the boxes do.
 *
 * Three.js is y-up where the room frame is z-up, so the room's x and y arrive
 * as x and -z. A turn of +yaw about the room's z is a turn of +yaw about
 * three's y under that swap.
 */
export function roomMeshPose(placement: RoomPlacement, capture: CapturePose = UNMOVED_CAPTURE) {
  const captureYaw = (capture.yaw_degrees * Math.PI) / 180;
  const dragYaw = (placement.yawDegrees * Math.PI) / 180;
  const cos = Math.cos(dragYaw);
  const sin = Math.sin(dragYaw);
  const fromCentroidX = capture.tx - placement.cx;
  const fromCentroidY = capture.ty - placement.cy;
  const x = placement.cx + placement.tx + fromCentroidX * cos - fromCentroidY * sin;
  const y = placement.cy + placement.ty + fromCentroidX * sin + fromCentroidY * cos;
  return { position: [x, 0, -y] as [number, number, number], yaw: captureYaw + dragYaw };
}

/** Where the owner has dragged one room: rotate about its centroid, then slide. */
export type RoomPlacement = {
  yawDegrees: number;
  tx: number;
  ty: number;
  cx: number;
  cy: number;
};

export const IDENTITY_PLACEMENT: RoomPlacement = { yawDegrees: 0, tx: 0, ty: 0, cx: 0, cy: 0 };

/**
 * The contract transform is row-major with translation at indices 3, 7, 11.
 *
 * Every column of the rotation turns with the room and the vertical row is left
 * alone, so a floor RoomPlan laid flat by tilting it stays flat. Rebuilding the
 * rotation from the heading alone stood every floor on its edge.
 */
function compose(m: number[], placement: RoomPlacement): number[] {
  const yaw = (placement.yawDegrees * Math.PI) / 180;
  const cos = Math.cos(yaw);
  const sin = Math.sin(yaw);
  const px = m[3] - placement.cx;
  const py = m[7] - placement.cy;
  const xRow = [0, 1, 2].map((column) => cos * m[column] - sin * m[4 + column]);
  const yRow = [0, 1, 2].map((column) => sin * m[column] + cos * m[4 + column]);
  return [
    ...xRow, placement.cx + px * cos - py * sin + placement.tx,
    ...yRow, placement.cy + px * sin + py * cos + placement.ty,
    m[8], m[9], m[10], m[11],
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
