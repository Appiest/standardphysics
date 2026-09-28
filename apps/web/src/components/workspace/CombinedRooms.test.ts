import { describe, expect, it } from "vitest";
import { isValidElement, type ReactElement, type ReactNode } from "react";
import { IDENTITY_PLACEMENT, roomMeshPose, type RoomGroup } from "@/lib/room-groups";
import { CombinedRooms } from "./CombinedRooms";
import { PaintedScan } from "./PaintedScan";

type PaintedScanElement = ReactElement<{ url: string }>;

function paintedScansIn(node: ReactNode): PaintedScanElement[] {
  if (Array.isArray(node)) return node.flatMap(paintedScansIn);
  if (!isValidElement<{ children?: ReactNode }>(node)) return [];
  if (node.type === PaintedScan) return [node as PaintedScanElement];
  return paintedScansIn(node.props.children);
}

const walk = (name: string, scanGlbUrl: string | null): RoomGroup => ({ name, node_ids: [], scan_glb_url: scanGlbUrl });

describe("the combined floor", () => {
  it("draws one captured mesh for every walk that has been photographed, and none for the rest", () => {
    const rooms = [walk("stacks", "/api/stacks.glb"), walk("lobby", null), walk("reading room", "/api/reading.glb")];

    const drawn = paintedScansIn(CombinedRooms({ rooms, placements: {} }));

    expect(drawn.map((scan) => scan.props.url)).toEqual(["/api/stacks.glb", "/api/reading.glb"]);
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
