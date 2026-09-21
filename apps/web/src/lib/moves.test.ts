import { describe, expect, it } from "vitest";
import type { SceneNode } from "@/types/contracts";
import { applyMoves, candidateMoves, moveNode, withMove } from "./moves";

const table: SceneNode = {
  id: "t", kind: "object", label: "Table", raw_category: "table", quality: "measured", movable: true,
  labeled_by: "roomplan", parent_id: null, dimensions: { x: 0.6, y: 0.6, z: 0.75 },
  transform: { m: [1, 0, 0, 2, 0, 1, 0, 2.2, 0, 0, 1, 0.375, 0, 0, 0, 1] },
};

const rounded = (values: number[]) => values.map((v) => +v.toFixed(6) + 0);

describe("moveNode", () => {
  it("translates on the floor and keeps height", () => {
    const moved = moveNode(table, { node_id: "t", delta_translation: { x: 0.127, y: -0.5, z: 0 }, delta_rotation_z_degrees: 0 });
    expect(rounded([moved.transform.m[3], moved.transform.m[7], moved.transform.m[11]])).toEqual([2.127, 1.7, 0.375]);
  });

  it("turns about the node's own centre, matching Lane C's move_node", () => {
    const moved = moveNode(table, { node_id: "t", delta_translation: { x: 0, y: 0, z: 0 }, delta_rotation_z_degrees: 90 });
    expect(rounded(moved.transform.m)).toEqual([0, -1, 0, 2, 1, 0, 0, 2.2, 0, 0, 1, 0.375, 0, 0, 0, 1]);
  });
});

describe("withMove", () => {
  it("adds a drag on top of the moves so far", () => {
    const once = withMove({}, "t", 0.1, 0, 15);
    const twice = withMove(once, "t", 0.05, 0.2, 15);
    expect(twice.t.delta_translation).toEqual({ x: 0.15000000000000002, y: 0.2, z: 0 });
    expect(twice.t.delta_rotation_z_degrees).toBe(30);
  });

  it("leaves a scene with no moves untouched", () => {
    const scene = { scan_id: "s", revision: 0, base_hash: null, nodes: [table] };
    expect(applyMoves(scene, {})).toBe(scene);
  });
});

describe("candidateMoves", () => {
  const scene = { scan_id: "s", revision: 0, base_hash: null, nodes: [table] };
  it("previews the exact screened translation and rotation through the existing move flow", () => {
    const candidate = applyMoves(scene, withMove({}, "t", 0.25, -0.1, 175));
    const moves = candidateMoves(scene, candidate)!;
    const preview = applyMoves(scene, Object.fromEntries(moves.map((move) => [move.node_id, move])));
    expect(rounded(preview.nodes[0].transform.m)).toEqual(rounded(candidate.nodes[0].transform.m));
    expect(preview.nodes[0].dimensions).toEqual(table.dimensions);
  });
  it("refuses resized, lifted, fixed or mismatched room candidates", () => {
    const resized = { ...table, dimensions: { ...table.dimensions, x: 2 } };
    const lifted = moveNode(table, { node_id: "t", delta_translation: { x: 0, y: 0, z: 1 }, delta_rotation_z_degrees: 0 });
    expect(candidateMoves(scene, { ...scene, nodes: [resized] })).toBeNull();
    expect(candidateMoves(scene, { ...scene, nodes: [lifted] })).toBeNull();
    expect(candidateMoves(scene, { ...scene, scan_id: "other" })).toBeNull();
    const fixed = { ...scene, nodes: [{ ...table, movable: false }] };
    expect(candidateMoves(fixed, applyMoves(fixed, withMove({}, "t", 1, 0, 0)))).toBeNull();
  });
});

describe("settling", () => {
  const floor: SceneNode = { ...table, id: "f", kind: "floor", label: "Floor", movable: false, dimensions: { x: 6, y: 6, z: 0.01 }, transform: { m: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] } };
  const laptop: SceneNode = { ...table, id: "l", label: "Laptop", dimensions: { x: 0.3, y: 0.2, z: 0.04 }, transform: { m: [1, 0, 0, 2, 0, 1, 0, 2.2, 0, 0, 1, 0.77, 0, 0, 0, 1] } };
  const scene = { scan_id: "s", revision: 0, base_hash: null, nodes: [floor, { ...table, movable: false }, laptop] };
  const heightOf = (moved: ReturnType<typeof applyMoves>, id: string) => +moved.nodes.find((node) => node.id === id)!.transform.m[11].toFixed(6);

  it("drops something lifted off a table onto the floor", () => {
    expect(heightOf(applyMoves(scene, withMove({}, "l", 2, 0, 0)), "l")).toBe(0.02);
  });

  it("keeps something on the table it slides across", () => {
    expect(heightOf(applyMoves(scene, withMove({}, "l", 0.1, 0, 0)), "l")).toBe(0.77);
  });

  it("leaves floor-standing furniture at its height", () => {
    const loose = { ...scene, nodes: [floor, table] };
    expect(heightOf(applyMoves(loose, withMove({}, "t", 1, 0, 0)), "t")).toBe(0.375);
  });
});
