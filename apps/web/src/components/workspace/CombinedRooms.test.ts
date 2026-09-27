import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { IDENTITY_PLACEMENT, roomMeshPose } from "@/lib/room-groups";

const here = join(process.cwd(), "src/components/workspace");

/**
 * The viewer hands the surfaces every prop it was given.
 *
 * Both the combine meshes and the active-room highlight were added, typechecked
 * and shipped dead, because the one place that renders them was handed a list
 * of props written out by name and neither was on it. Optional props make that
 * silent: nothing fails to compile, and the feature simply never appears.
 */
describe("the viewer forwards what it is given", () => {
  it("passes its whole props object to the surfaces, not a hand-written list", () => {
    const source = readFileSync(join(here, "Viewer.tsx"), "utf8");
    const call = source.match(/<ShopSurfaces([^/]*)\/>/);
    expect(call, "Viewer should render ShopSurfaces").not.toBeNull();
    expect(call![1]).toContain("{...viewerProps}");
  });

  it("draws one captured mesh for every walk that has been photographed", () => {
    const source = readFileSync(join(here, "CombinedRooms.tsx"), "utf8");
    expect(source).toContain("room.scan_glb_url");
    expect(source).toContain("PaintedScan");
  });
});

describe("a walk's mesh stands where its boxes stand", () => {
  it("leaves an unplaced walk exactly where it was captured", () => {
    const { position, yaw } = roomMeshPose(IDENTITY_PLACEMENT);
    expect(position).toEqual([0, 0, -0]);
    expect(yaw).toBe(0);
  });

  it("slides a walk by the distance it was dragged", () => {
    const { position, yaw } = roomMeshPose({ yawDegrees: 0, tx: 3, ty: -2, cx: 0, cy: 0 });
    expect(position[0]).toBeCloseTo(3);
    expect(position[2]).toBeCloseTo(2);
    expect(yaw).toBe(0);
  });

  it("turns a walk about the point it was turned about, not about the origin", () => {
    // A quarter turn about (1, 0) leaves that point alone.
    const { position, yaw } = roomMeshPose({ yawDegrees: 90, tx: 0, ty: 0, cx: 1, cy: 0 });
    const turned = [
      position[0] + (1 * Math.cos(yaw) - 0 * Math.sin(yaw)),
      1 * Math.sin(yaw) + 0 * Math.cos(yaw) - position[2],
    ];
    expect(turned[0]).toBeCloseTo(1);
    expect(turned[1]).toBeCloseTo(0);
  });
});
