import { Matrix4, Vector3 } from "three";
import { describe, expect, it } from "vitest";
import { toSplatViewerMatrix } from "./GaussianSplatScan";

const translation = (x: number, y: number, z: number) => [
  1, 0, 0, x,
  0, 1, 0, y,
  0, 0, 1, z,
  0, 0, 0, 1,
];

const position = (matrix: Matrix4) => new Vector3().setFromMatrixPosition(matrix).toArray();

describe("toSplatViewerMatrix", () => {
  it("applies an asset Z-up alignment directly before converting into viewer coordinates", () => {
    const matrix = toSplatViewerMatrix(translation(4, 5, 6));
    expect(position(matrix)).toEqual([4, 6, -5]);
    expect(new Vector3(0, 0, 2).applyMatrix4(matrix).toArray()).toEqual([4, 8, -5]);
  });

  it("rejects malformed asset alignment data before allocating splat resources", () => {
    expect(() => toSplatViewerMatrix([1, 2, 3])).toThrow("finite row-major 4x4 matrix");
  });
});
