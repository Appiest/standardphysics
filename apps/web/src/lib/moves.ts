import type { Mat4, NodeMove, SceneGraph, SceneNode } from "@/types/contracts";

export type MoveSet = Record<string, NodeMove>;

/** Mirrors Lane C's move_node: turn about the node's own centre, then translate on the floor. */
export function moveNode(node: SceneNode, move: NodeMove): SceneNode {
  const m = node.transform.m;
  const angle = Math.atan2(m[4], m[0]) + (move.delta_rotation_z_degrees * Math.PI) / 180;
  const [cos, sin] = [Math.cos(angle), Math.sin(angle)];
  const x = m[3] + move.delta_translation.x;
  const y = m[7] + move.delta_translation.y;
  const z = m[11] + move.delta_translation.z;
  const transform = { m: [cos, -sin, 0, x, sin, cos, 0, y, 0, 0, 1, z, 0, 0, 0, 1] } as Mat4;
  return { ...node, transform };
}

/** Mirrors Lane C's RESTING_GAP: an underside higher than this above the floor was resting on something. */
const RESTING_GAP = 0.12;

function floorHeight(scene: SceneGraph): number {
  return scene.nodes.find((node) => node.kind === "floor")?.transform.m[11] ?? 0;
}

function underside(node: SceneNode): number {
  return node.transform.m[11] - node.dimensions.z / 2;
}

/** Mirrors Lane C's rests_on_something: a piece held up off the floor by whatever is under it. */
export function restsOnSomething(node: SceneNode, floorZ: number): boolean {
  return node.kind === "object" && underside(node) > floorZ + RESTING_GAP;
}

function coversPoint(node: SceneNode, x: number, y: number): boolean {
  const m = node.transform.m;
  const [cos, sin] = [m[0], m[4]];
  const [dx, dy] = [x - m[3], y - m[7]];
  const localX = dx * cos + dy * sin;
  const localY = -dx * sin + dy * cos;
  return Math.abs(localX) <= node.dimensions.x / 2 && Math.abs(localY) <= node.dimensions.y / 2;
}

/** Mirrors Lane C's RIDING_GAP: an underside this close to another piece's top is sitting on it. */
const RIDING_GAP = 0.05;

function topOf(node: SceneNode): number {
  return node.transform.m[11] + node.dimensions.z / 2;
}

/** Mirrors Lane C's test in riders_of: the rider's underside is on the carrier's top, over its footprint. */
export function sitsOn(rider: SceneNode, carrier: SceneNode): boolean {
  return rider.id !== carrier.id && rider.kind === "object" &&
    Math.abs(underside(rider) - topOf(carrier)) <= RIDING_GAP && coversPoint(carrier, rider.transform.m[3], rider.transform.m[7]);
}

/** Mirrors Lane C's riders_of: what sits on the carrier's top, like a register on a counter. */
export function ridersOf(scene: SceneGraph, carrier: SceneNode): SceneNode[] {
  return scene.nodes.filter((node) => sitsOn(node, carrier));
}

/** The piece this one sits on and travels with when it moves, if any. */
export function supportOf(scene: SceneGraph, node: SceneNode): SceneNode | null {
  return scene.nodes.find((carrier) => carrier.kind === "object" && sitsOn(node, carrier)) ?? null;
}

/** The rider's share of the carrier's move: it turns about the carrier's centre, not its own. */
function riding(carrier: SceneNode, rider: SceneNode, move: NodeMove): NodeMove {
  const [cx, cy] = [carrier.transform.m[3], carrier.transform.m[7]];
  const [ox, oy] = [rider.transform.m[3] - cx, rider.transform.m[7] - cy];
  const angle = (move.delta_rotation_z_degrees * Math.PI) / 180;
  const [tx, ty] = [ox * Math.cos(angle) - oy * Math.sin(angle), ox * Math.sin(angle) + oy * Math.cos(angle)];
  return {
    node_id: rider.id,
    delta_translation: { x: move.delta_translation.x + tx - ox, y: move.delta_translation.y + ty - oy, z: 0 },
    delta_rotation_z_degrees: move.delta_rotation_z_degrees,
  };
}

/** Mirrors Lane C's carried_along: the moves plus one for everything sitting on a moved piece. */
export function carriedAlong(scene: SceneGraph, moves: MoveSet): MoveSet {
  const all: MoveSet = { ...moves };
  for (const move of Object.values(moves)) {
    const carrier = scene.nodes.find((node) => node.id === move.node_id);
    for (const rider of carrier ? ridersOf(scene, carrier) : []) {
      all[rider.id] ??= riding(carrier!, rider, move);
    }
  }
  return all;
}

/** Mirrors Lane C's settle: sit on the highest top under the piece's centre, or on the floor. */
function settle(scene: SceneGraph, node: SceneNode, floorZ: number): SceneNode {
  const [x, y] = [node.transform.m[3], node.transform.m[7]];
  const surface = scene.nodes
    .filter((other) => other.id !== node.id && other.kind === "object" && coversPoint(other, x, y))
    .reduce((highest, other) => Math.max(highest, other.transform.m[11] + other.dimensions.z / 2), floorZ);
  const m = [...node.transform.m];
  m[11] = surface + node.dimensions.z / 2;
  return { ...node, transform: { m } as Mat4 };
}

export function applyMoves(scene: SceneGraph, chosen: MoveSet): SceneGraph {
  if (Object.keys(chosen).length === 0) return scene;
  const moves = carriedAlong(scene, chosen);
  const floorZ = floorHeight(scene);
  const resting = new Set(scene.nodes.filter((node) => moves[node.id] && restsOnSomething(node, floorZ)).map((node) => node.id));
  const moved = { ...scene, nodes: scene.nodes.map((node) => (moves[node.id] ? moveNode(node, moves[node.id]) : node)) };
  return { ...moved, nodes: moved.nodes.map((node) => (resting.has(node.id) ? settle(moved, node, floorZ) : node)) };
}

function sameEnvelope(node: SceneNode, next: SceneNode | undefined): next is SceneNode {
  return !!next && next.kind === node.kind && next.movable === node.movable &&
    (["x", "y", "z"] as const).every((axis) => node.dimensions[axis] === next.dimensions[axis]);
}

function floorMove(node: SceneNode, next: SceneNode): NodeMove | null {
  if (!node.movable || node.kind !== "object") return null;
  const m = node.transform.m, n = next.transform.m;
  const angle = Math.atan2(n[4], n[0]) - Math.atan2(m[4], m[0]);
  const move: NodeMove = {
    node_id: node.id,
    delta_translation: { x: n[3] - m[3], y: n[7] - m[7], z: 0 },
    delta_rotation_z_degrees: Math.atan2(Math.sin(angle), Math.cos(angle)) * 180 / Math.PI,
  };
  const HEIGHT = 11;
  return moveNode(node, move).transform.m.every((value, index) => index === HEIGHT || Math.abs(value - n[index]) < 1e-6) ? move : null;
}

function sameHeights(settled: SceneGraph, candidate: SceneGraph): boolean {
  const heights = new Map(candidate.nodes.map((node) => [node.id, node.transform.m[11]]));
  return settled.nodes.every((node) => Math.abs(node.transform.m[11] - (heights.get(node.id) ?? Number.NaN)) < 1e-6);
}

function sameRoom(base: SceneGraph, candidate: SceneGraph): boolean {
  return base.scan_id === candidate.scan_id && base.nodes.length === candidate.nodes.length;
}

type MoveOutcome = NodeMove | "unchanged" | null;

function moveTo(node: SceneNode, next: SceneNode | undefined): MoveOutcome {
  if (!sameEnvelope(node, next)) return null;
  if (next.transform.m.some((value) => !Number.isFinite(value))) return null;
  if (node.transform.m.every((value, index) => Math.abs(value - next.transform.m[index]) < 1e-6)) return "unchanged";
  return floorMove(node, next);
}

function movesBetween(base: SceneGraph, candidate: SceneGraph): NodeMove[] | null {
  const proposed = new Map(candidate.nodes.map((node) => [node.id, node]));
  if (proposed.size !== base.nodes.length) return null;
  const moves: NodeMove[] = [];
  for (const node of base.nodes) {
    const outcome = moveTo(node, proposed.get(node.id));
    if (outcome === null) return null;
    if (outcome !== "unchanged") moves.push(outcome);
  }
  return moves;
}

/** Recover only floor moves from a screened candidate for the existing review/save flow. */
export function candidateMoves(base: SceneGraph, candidate: SceneGraph): NodeMove[] | null {
  if (!sameRoom(base, candidate)) return null;
  const moves = movesBetween(base, candidate);
  if (!moves) return null;
  return sameHeights(applyMoves(base, Object.fromEntries(moves.map((move) => [move.node_id, move]))), candidate) ? moves : null;
}

export function withMove(moves: MoveSet, nodeId: string, dx: number, dy: number, degrees: number): MoveSet {
  const current = moves[nodeId] ?? { node_id: nodeId, delta_translation: { x: 0, y: 0, z: 0 }, delta_rotation_z_degrees: 0 };
  return {
    ...moves,
    [nodeId]: {
      node_id: nodeId,
      delta_translation: { x: current.delta_translation.x + dx, y: current.delta_translation.y + dy, z: 0 },
      delta_rotation_z_degrees: (current.delta_rotation_z_degrees + degrees) % 360,
    },
  };
}

export const METERS_PER_INCH = 0.0254;

const NUDGE_DIRECTIONS: Record<string, [number, number]> = {
  ArrowUp: [0, 1],
  ArrowDown: [0, -1],
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
};

const TURN_STEP_DEGREES = 15;

type KeyPress = Pick<KeyboardEvent, "key" | "shiftKey" | "preventDefault">;

/** A step the owner sees as right and up on a plan drawn turned by `turnDegrees`, as a step across the room's floor. */
export function screenStepInRoom(right: number, up: number, turnDegrees: number): [number, number] {
  const radians = (turnDegrees * Math.PI) / 180;
  const [cos, sin] = [Math.cos(radians), Math.sin(radians)];
  return [cos * right - sin * up, sin * right + cos * up];
}

/**
 * Arrow keys slide the piece in hand an inch, six with Shift; R turns it a step, the other way with Shift.
 * On a plan drawn turned by `turnDegrees`, the arrows follow the screen rather than the room.
 */
export function nudgeForKey(event: KeyPress, nudge: (dx: number, dy: number, degrees: number) => void, turnDegrees = 0): boolean {
  const inches = (event.shiftKey ? 6 : 1) * METERS_PER_INCH;
  const direction = NUDGE_DIRECTIONS[event.key];
  if (direction) {
    event.preventDefault();
    const [dx, dy] = screenStepInRoom(direction[0] * inches, direction[1] * inches, turnDegrees);
    nudge(dx, dy, 0);
    return true;
  }
  if (event.key.toLowerCase() !== "r") return false;
  event.preventDefault();
  nudge(0, 0, event.shiftKey ? -TURN_STEP_DEGREES : TURN_STEP_DEGREES);
  return true;
}
