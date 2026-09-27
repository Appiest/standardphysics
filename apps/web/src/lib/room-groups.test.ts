import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { Mat4, SceneNode } from "@/types/contracts";
import { moveNode, roomMeshPose, type CapturePose, type RoomPlacement } from "./room-groups";

function nodeAt(x: number, y: number): SceneNode {
  return { id: "n", transform: { m: [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, 0, 0, 0, 0, 1] } as Mat4 } as SceneNode;
}

/** Where three.js draws a capture-frame point once its group takes the mesh pose. */
function drawnAt(pose: ReturnType<typeof roomMeshPose>, x: number, y: number): [number, number] {
  const threeX = x;
  const threeZ = -y;
  const cos = Math.cos(pose.yaw);
  const sin = Math.sin(pose.yaw);
  const turnedX = threeX * cos + threeZ * sin;
  const turnedZ = -threeX * sin + threeZ * cos;
  return [turnedX + pose.position[0], -(turnedZ + pose.position[2])];
}

function capturedToPlaced(capture: CapturePose, x: number, y: number): [number, number] {
  const yaw = (capture.yaw_degrees * Math.PI) / 180;
  return [Math.cos(yaw) * x - Math.sin(yaw) * y + capture.tx, Math.sin(yaw) * x + Math.cos(yaw) * y + capture.ty];
}

describe("roomMeshPose", () => {
  const capture: CapturePose = { yaw_degrees: -58, tx: 27.85, ty: 15.47 };
  const drag: RoomPlacement = { yawDegrees: 30, tx: 2, ty: -1, cx: 25, cy: 14 };

  it("draws a walk's mesh on top of its boxes, whatever the capture and the drag", () => {
    for (const [x, y] of [[0, 0], [4, 0], [1.5, -3]]) {
      const [placedX, placedY] = capturedToPlaced(capture, x, y);
      const box = moveNode(nodeAt(placedX, placedY), drag).transform.m;
      const [meshX, meshY] = drawnAt(roomMeshPose(drag, capture), x, y);
      expect(meshX).toBeCloseTo(box[3], 9);
      expect(meshY).toBeCloseTo(box[7], 9);
    }
  });

  it("turns the mesh the same way as the boxes", () => {
    const turn: RoomPlacement = { yawDegrees: 90, tx: 0, ty: 0, cx: 0, cy: 0 };
    const [meshX, meshY] = drawnAt(roomMeshPose(turn), 1, 0);
    expect(meshX).toBeCloseTo(0, 9);
    expect(meshY).toBeCloseTo(1, 9);
  });
});

/** The real test1 scan's floor, carried from ARKit's column-major Y-up matrix into the room frame as ingest does. */
function realFloor(): SceneNode {
  const payload = JSON.parse(readFileSync(join(__dirname, "../../../../datasets/phone/test1/room.json"), "utf8"));
  const c: number[] = payload.floors[0].transform;
  const rows = [0, 1, 2, 3].map((r) => [c[r], c[4 + r], c[8 + r], c[12 + r]]);
  const [x, y, z, w] = rows;
  const zUp = [x, z.map((v) => -v), y, w];
  const m = zUp.flatMap((row) => [row[0], -row[2], row[1], row[3]]);
  return { id: "floor", transform: { m } as Mat4 } as SceneNode;
}

describe("moveNode", () => {
  it("turns a real floor about the vertical without standing it up", () => {
    const floor = realFloor();
    expect(Math.abs(floor.transform.m[10])).toBeLessThan(0.5);
    const moved = moveNode(floor, { yawDegrees: 92, tx: 10, ty: 5, cx: 0, cy: 0 }).transform.m;
    const cos = Math.cos((92 * Math.PI) / 180);
    const sin = Math.sin((92 * Math.PI) / 180);
    const m = floor.transform.m;
    for (const column of [0, 1, 2]) {
      expect(moved[column]).toBeCloseTo(cos * m[column] - sin * m[4 + column], 6);
      expect(moved[4 + column]).toBeCloseTo(sin * m[column] + cos * m[4 + column], 6);
      expect(moved[8 + column]).toBeCloseTo(m[8 + column], 6);
    }
  });
});
