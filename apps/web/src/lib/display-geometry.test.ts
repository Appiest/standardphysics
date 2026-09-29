import { BoxGeometry, Matrix4 } from "three";
import { describe, expect, it } from "vitest";
import { canUseCapturedGlbGeometry, displayScale, drawnInModel, hasUsableFloorMesh, MAX_DISPLAY_WALL_HEIGHT, MIN_DISPLAY_WALL_THICKNESS, needsDisplayBoxFallback } from "./display-geometry";
import type { SceneNode } from "@/types/contracts";

const wall: SceneNode = {
  id: "wall", kind: "wall", label: "Wall", raw_category: "wall",
  dimensions: { x: 4, y: 0, z: 2.4 },
  transform: { m: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1.2, 0, 0, 0, 1] },
  quality: "measured", movable: false, labeled_by: "roomplan", parent_id: null,
};

describe("display-only wall geometry", () => {
  it("gives a zero-thickness measured wall a visible shell without rewriting its dimensions", () => {
    expect(displayScale(wall)).toEqual([4, 2.4, MIN_DISPLAY_WALL_THICKNESS]);
    expect(wall.dimensions.y).toBe(0);
  });

  it("clamps tall walls to MAX_DISPLAY_WALL_HEIGHT for display", () => {
    const tallWall: SceneNode = { ...wall, dimensions: { x: 4, y: 0, z: 3.8 } };
    expect(displayScale(tallWall)).toEqual([4, MAX_DISPLAY_WALL_HEIGHT, MIN_DISPLAY_WALL_THICKNESS]);
  });

  it("replaces cached GLB wall geometry with a singular mesh or transform", () => {
    const box = new BoxGeometry(1, 1, 1);
    expect(needsDisplayBoxFallback(wall, box, new Matrix4().makeScale(1, 1, 0))).toBe(true);
    expect(needsDisplayBoxFallback(wall, box, new Matrix4())).toBe(false);
    box.dispose();
  });
});

describe("photo floor geometry", () => {
  it("keeps a flat floor with an invertible placement and rejects a singular cached GLB", () => {
    const floor = new BoxGeometry(1, 0, 1);
    expect(hasUsableFloorMesh(floor, new Matrix4())).toBe(true);
    expect(hasUsableFloorMesh(floor, new Matrix4().makeScale(1, 0, 1))).toBe(false);
    expect(hasUsableFloorMesh(floor, new Matrix4().set(1, 0, 0, 0, 0, Number.NaN, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1))).toBe(false);
    floor.dispose();
  });

  it("does not reuse an old baked shape for a stale current node", () => {
    const box = new BoxGeometry(1, 1, 1);
    expect(canUseCapturedGlbGeometry(wall, box, new Matrix4(), false)).toBe(true);
    expect(canUseCapturedGlbGeometry(wall, box, new Matrix4(), true)).toBe(false);
    box.dispose();
  });
});

describe("what the model draws from a real scan", () => {
  const shareTea = (node: Pick<SceneNode, "kind" | "label" | "raw_category" | "dimensions"> & { m: SceneNode["transform"]["m"] }): SceneNode => ({
    ...wall, id: node.label, kind: node.kind, label: node.label, raw_category: node.raw_category,
    dimensions: node.dimensions, transform: { m: node.m }, labeled_by: "lidar",
  });
  const shopWall = shareTea({ kind: "wall", label: "Wall", raw_category: "wall", dimensions: { x: 9.67151, y: 0, z: 3.1866665 },
    m: [0.88593596, 0.4638077, 0, -0.55621326, -0.4638077, 0.88593596, 0, 4.628888, 0, 0, 1, 1.5933332, 0, 0, 0, 1] });
  const ceiling = shareTea({ kind: "ceiling", label: "Ceiling", raw_category: "lidar_ceiling", dimensions: { x: 7.394203, y: 6.205668, z: 0.03 },
    m: [1, 0, 0, -1.043172, 0, 1, 0, 1.742806, 0, 0, 1, 3.768037, 0, 0, 0, 1] });
  const unclaimed = shareTea({ kind: "surface_candidate", label: "Unidentified vertical surface", raw_category: "lidar_candidate",
    dimensions: { x: 10.180636, y: 9.601639, z: 4.151214 }, m: [1, 0, 0, -1.648945, 0, 1, 0, 1.292984, 0, 0, 1, 2.012777, 0, 0, 0, 1] });
  const floor = shareTea({ kind: "floor", label: "Floor", raw_category: "floor", dimensions: { x: 9, y: 8, z: 0 },
    m: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] });

  it("leaves out Share Tea's room-sized box round unclaimed LiDAR and its ceiling, and keeps the walls and floor", () => {
    const drawn = drawnInModel([shopWall, ceiling, unclaimed, floor]);
    expect(drawn.map((node) => node.label)).toEqual(["Wall", "Floor"]);
  });

  it("keeps a flat sheet when the scan has no walls to say where the room's top is", () => {
    expect(drawnInModel([ceiling, floor])).toEqual([ceiling, floor]);
  });
});
