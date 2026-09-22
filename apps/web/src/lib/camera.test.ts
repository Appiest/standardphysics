import { describe, expect, it } from "vitest";
import { poseAtPoint } from "@/lib/camera";
import type { SceneGraph } from "@/types/contracts";

const scene = (): SceneGraph => ({
  scan_id: "scan-1",
  revision: 0,
  base_hash: null,
  nodes: [
    {
      id: "wall-1",
      kind: "wall",
      label: "Wall",
      raw_category: "wall",
      dimensions: { x: 6, y: 0.2, z: 3 },
      transform: { m: [1, 0, 0, 4, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] },
      quality: "measured",
      movable: false,
      labeled_by: "roomplan",
      parent_id: null,
    },
    {
      id: "wall-2",
      kind: "wall",
      label: "Wall",
      raw_category: "wall",
      dimensions: { x: 0.2, y: 6, z: 3 },
      transform: { m: [1, 0, 0, -2, 0, 1, 0, 4, 0, 0, 1, 0, 0, 0, 0, 1] },
      quality: "measured",
      movable: false,
      labeled_by: "roomplan",
      parent_id: null,
    },
  ],
});

describe("poseAtPoint", () => {
  it("targets the point in the viewer frame (x, z up, -y depth)", () => {
    const pose = poseAtPoint({ x: 1, y: 2, z: 1.2 }, scene());
    expect(pose.target[0]).toBeCloseTo(1, 6);
    expect(pose.target[1]).toBeCloseTo(1.2, 6);
    expect(pose.target[2]).toBeCloseTo(-2, 6);
  });

  it("stands the camera off the point and above it rather than inside it", () => {
    const pose = poseAtPoint({ x: 1, y: 2, z: 1.2 }, scene());
    const depth = pose.position[2] - pose.target[2];
    expect(depth).toBeGreaterThan(0);
    expect(pose.position[1]).toBeGreaterThan(pose.target[1]);
  });

  it("keeps the eye above the floor even for a point at floor level", () => {
    const pose = poseAtPoint({ x: 0, y: 0, z: 0 }, scene());
    expect(pose.position[1]).toBeGreaterThan(0);
  });
});
