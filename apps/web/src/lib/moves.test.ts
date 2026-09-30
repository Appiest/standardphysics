import { describe, expect, it } from "vitest";
import type { SceneGraph, SceneNode } from "@/types/contracts";
import {
  applyMoves, candidateMoves, METERS_PER_INCH, moveNode, nudgeForKey, restsOnSomething, ridersOf, screenStepInRoom, sitsOn, supportOf, withMove,
} from "./moves";

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

describe("nudgeForKey", () => {
  const press = (key: string, shiftKey = false) => ({ key, shiftKey, preventDefault: () => {} });

  it("slides an inch with an arrow, six with Shift, and turns a step with R", () => {
    const nudges: number[][] = [];
    const record = (dx: number, dy: number, degrees: number) => nudges.push([dx, dy, degrees]);
    expect(nudgeForKey(press("ArrowUp"), record)).toBe(true);
    nudgeForKey(press("ArrowLeft", true), record);
    nudgeForKey(press("r"), record);
    nudgeForKey(press("R", true), record);
    expect(nudges).toEqual([[0, METERS_PER_INCH, 0], [-6 * METERS_PER_INCH, 0, 0], [0, 0, 15], [0, 0, -15]]);
  });

  it("leaves every other key alone", () => {
    expect(nudgeForKey(press("Tab"), () => { throw new Error("moved"); })).toBe(false);
  });
});

describe("carrying what sits on a piece", () => {
  const cup: SceneNode = { ...table, id: "c", label: "Cup", dimensions: { x: 0.1, y: 0.1, z: 0.12 },
    transform: { m: [1, 0, 0, 2.2, 0, 1, 0, 2.2, 0, 0, 1, 0.81, 0, 0, 0, 1] } };
  const scene: SceneGraph = { scan_id: "s", revision: 0, base_hash: null, nodes: [table, cup] };
  const where = (moved: SceneGraph, id: string) => {
    const m = moved.nodes.find((node) => node.id === id)!.transform.m;
    return rounded([m[3], m[7], m[11]]);
  };

  it("slides the cup with the table it sits on", () => {
    const moved = applyMoves(scene, { t: { node_id: "t", delta_translation: { x: 1, y: 0, z: 0 }, delta_rotation_z_degrees: 0 } });
    expect(where(moved, "c")).toEqual([3.2, 2.2, 0.81]);
  });

  it("swings the cup about the table's centre when the table turns", () => {
    const moved = applyMoves(scene, { t: { node_id: "t", delta_translation: { x: 0, y: 0, z: 0 }, delta_rotation_z_degrees: 90 } });
    expect(where(moved, "c")).toEqual([2, 2.4, 0.81]);
  });
});

describe("what a piece sits on", () => {
  const cup: SceneNode = { ...table, id: "c", label: "Cup", dimensions: { x: 0.1, y: 0.1, z: 0.12 },
    transform: { m: [1, 0, 0, 2.2, 0, 1, 0, 2.2, 0, 0, 1, 0.81, 0, 0, 0, 1] } };
  const drawer: SceneNode = { ...table, id: "d", label: "Drawer", dimensions: { x: 0.4, y: 0.3, z: 0.2 },
    transform: { m: [1, 0, 0, 2, 0, 1, 0, 2.2, 0, 0, 1, 0.5, 0, 0, 0, 1] } };
  const scene: SceneGraph = { scan_id: "s", revision: 0, base_hash: null, nodes: [table, cup, drawer] };

  it("finds the table under a cup resting on its top, as Lane C's riders_of does", () => {
    expect(sitsOn(cup, table)).toBe(true);
    expect(supportOf(scene, cup)?.id).toBe("t");
    expect(ridersOf(scene, table).map((node) => node.id)).toEqual(["c"]);
  });

  it("finds nothing under a drawer inside a case, though it rests off the floor", () => {
    expect(supportOf(scene, drawer)).toBeNull();
    expect(restsOnSomething(drawer, 0)).toBe(true);
    expect(restsOnSomething(table, 0)).toBe(false);
  });
});

describe("arrows on a turned plan", () => {
  const press = (key: string) => ({ key, shiftKey: false, preventDefault: () => {} });

  it("slides the way the screen shows, not the way the room runs", () => {
    const nudges: number[][] = [];
    nudgeForKey(press("ArrowRight"), (dx, dy) => nudges.push(rounded([dx, dy])), 90);
    nudgeForKey(press("ArrowUp"), (dx, dy) => nudges.push(rounded([dx, dy])), 90);
    expect(nudges).toEqual([[0, METERS_PER_INCH], [-METERS_PER_INCH, 0]]);
  });

  it("matches the room when the plan is not turned", () => {
    expect(rounded(screenStepInRoom(1, 2, 0))).toEqual([1, 2]);
  });
});
