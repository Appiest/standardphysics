import { Mesh, type Object3D } from "three";

/** Groups every glTF primitive under its measured node's UUID-named parent. */
export function groupGlbPrimitives(scene: Object3D, nodeIds: Set<string>): Map<string, Mesh[]> {
  const groups = new Map<string, Mesh[]>();
  scene.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    let owner: Object3D | null = object;
    while (owner && !nodeIds.has(owner.name)) owner = owner.parent;
    if (!owner) return;
    const primitives = groups.get(owner.name) ?? [];
    primitives.push(object);
    groups.set(owner.name, primitives);
  });
  return groups;
}
