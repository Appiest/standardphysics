import { BufferAttribute, BufferGeometry, Matrix4 } from "three";
import { describe, expect, it } from "vitest";
import type { Mat4, SceneNode } from "@/types/contracts";
import { carveGeometry, carveRegion } from "./carve-scan";

/** A counter 2 m long, 0.6 m deep and 1 m tall, standing on the floor at scene (3, 4). */
const counter = {
  id: "counter", kind: "object", label: "Counter", raw_category: "table", quality: "measured", movable: true,
  dimensions: { x: 2, y: 0.6, z: 1 },
  transform: { m: [1, 0, 0, 3, 0, 1, 0, 4, 0, 0, 1, 0.5, 0, 0, 0, 1] } as Mat4,
} as SceneNode;

/** A small triangle centred on a viewer-space point (y up, scene y becomes -z). */
function triangleAt(x: number, y: number, z: number): number[] {
  return [x - 0.01, y, z, x + 0.01, y, z, x, y + 0.01, z];
}

function scan(...triangles: number[][]): BufferGeometry {
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(new Float32Array(triangles.flat()), 3));
  return geometry;
}

describe("carving a piece out of the scan", () => {
  const regions = [carveRegion(counter)];

  it("gives the piece its top and sides and leaves the floor and the room behind", () => {
    const top = triangleAt(3, 0.99, -4);
    const side = triangleAt(3.9, 0.5, -4.25);
    const floorUnder = triangleAt(3, 0, -4);
    const wall = triangleAt(0, 1, 0);
    const carved = carveGeometry(scan(top, side, floorUnder, wall), new Matrix4(), regions);

    expect(carved.pieces.get("counter")?.index?.count).toBe(6);
    expect(carved.room?.index?.count).toBe(6);
  });

  it("reaches a few centimetres past the measured box, where the scanned edge really is", () => {
    const justOutside = triangleAt(4.04, 0.5, -4);
    const farOutside = triangleAt(4.2, 0.5, -4);
    const carved = carveGeometry(scan(justOutside, farOutside), new Matrix4(), regions);

    expect(carved.pieces.get("counter")?.index?.count).toBe(3);
    expect(carved.room?.index?.count).toBe(3);
  });

  it("shares the scan's vertices instead of copying them", () => {
    const source = scan(triangleAt(3, 0.5, -4));
    const carved = carveGeometry(source, new Matrix4(), regions);

    expect(carved.pieces.get("counter")?.getAttribute("position")).toBe(source.getAttribute("position"));
    expect(carved.room).toBeNull();
  });
});
