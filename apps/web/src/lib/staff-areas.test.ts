import { describe, expect, it } from "vitest";
import { cornerPoint, moveArea, resizeArea } from "./staff-areas";
import type { StaffArea } from "@/types/contracts";

const kitchen: StaffArea = { name: "Kitchen", centre: { x: 0, y: 0, z: 0 }, width: 4, depth: 2, rotation_z_degrees: 90 };

describe("staff areas", () => {
  it("keeps the opposite corner still while one corner is dragged", () => {
    const corner = { alongSign: 1, acrossSign: 1 } as const;
    const opposite = cornerPoint(kitchen, { alongSign: -1, acrossSign: -1 });
    const resized = resizeArea(kitchen, corner, { x: -3, y: 4 });
    const after = cornerPoint(resized, { alongSign: -1, acrossSign: -1 });
    expect(after.x).toBeCloseTo(opposite.x);
    expect(after.y).toBeCloseTo(opposite.y);
    expect(resized.width).toBeCloseTo(6);
    expect(resized.depth).toBeCloseTo(4);
  });

  it("never shrinks past half a metre", () => {
    const resized = resizeArea(kitchen, { alongSign: 1, acrossSign: 1 }, { x: 5, y: -5 });
    expect(resized.width).toBe(0.5);
    expect(resized.depth).toBe(0.5);
  });

  it("moves without changing size", () => {
    expect(moveArea(kitchen, 1, 2)).toMatchObject({ centre: { x: 1, y: 2 }, width: 4, depth: 2 });
  });
});
