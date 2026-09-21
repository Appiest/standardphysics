import { BoxGeometry, Matrix4 } from "three";
import { describe, expect, it } from "vitest";
import { canUseCapturedGlbGeometry, displayScale, hasUsableFloorMesh, MAX_DISPLAY_WALL_HEIGHT, MIN_DISPLAY_WALL_THICKNESS, needsDisplayBoxFallback } from "./display-geometry";
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
