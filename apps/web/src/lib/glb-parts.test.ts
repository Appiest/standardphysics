import { BoxGeometry, Group, Mesh, MeshStandardMaterial } from "three";
import { describe, expect, it } from "vitest";
import { groupGlbPrimitives } from "./glb-parts";

describe("groupGlbPrimitives", () => {
  it("keeps every primitive under a UUID-named reconstructed object", () => {
    const root = new Group();
    const object = new Group();
    object.name = "node-uuid";
    const body = new Mesh(new BoxGeometry(), new MeshStandardMaterial({ color: "#336699" }));
    const handle = new Mesh(new BoxGeometry(), new MeshStandardMaterial({ color: "#cccccc" }));
    handle.position.set(0.4, 0, 0);
    object.add(body, handle);
    root.add(object);
    root.updateMatrixWorld(true);

    const groups = groupGlbPrimitives(root, new Set(["node-uuid"]));
    expect(groups.get("node-uuid")).toEqual([body, handle]);
    expect(groups.get("node-uuid")?.[1].matrixWorld.elements[12]).toBeCloseTo(0.4);
    body.geometry.dispose();
    handle.geometry.dispose();
    (body.material as MeshStandardMaterial).dispose();
    (handle.material as MeshStandardMaterial).dispose();
  });
});
