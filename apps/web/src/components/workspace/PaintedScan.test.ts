import { BackSide, BufferAttribute, BufferGeometry, DataTexture, LinearFilter, LinearMipmapLinearFilter, Mesh, MeshBasicMaterial, MeshStandardMaterial, RGBAFormat, UnsignedByteType } from "three";
import { describe, expect, it } from "vitest";
import { hasVertexColors, paintedMaterial, paintedMaterials } from "./PaintedScan";

function texture() {
  return new DataTexture(new Uint8Array([255, 255, 255, 255]), 1, 1, RGBAFormat, UnsignedByteType);
}

describe("painted scan materials", () => {
  it("keeps the UV photograph and vertex colors without mutating the GLTF material", () => {
    const map = texture();
    const alphaMap = texture();
    const source = new MeshStandardMaterial({ map, alphaMap, opacity: 0.6, transparent: true, side: BackSide });

    const painted = paintedMaterial(source, true);

    expect(painted).toBeInstanceOf(MeshBasicMaterial);
    expect(painted.map?.image).toBe(map.image);
    expect(painted.alphaMap).toBe(alphaMap);
    expect(painted.vertexColors).toBe(true);
    expect(painted.opacity).toBe(0.6);
    expect(painted.transparent).toBe(true);
    expect(painted.side).toBe(BackSide);
    expect(source.map).toBe(map);
    expect(source.alphaMap).toBe(alphaMap);
    expect(source.vertexColors).toBe(false);
  });

  it("uses an emissive photograph when it is the unlit source and preserves material groups", () => {
    const emissiveMap = texture();
    const source = new MeshStandardMaterial({ emissiveMap });
    const group = paintedMaterials([source, new MeshBasicMaterial({ vertexColors: true })], false);

    expect(Array.isArray(group)).toBe(true);
    if (!Array.isArray(group)) throw new Error("expected material group");
    expect(group).toHaveLength(2);
    expect((group[0] as MeshBasicMaterial).map?.image).toBe(emissiveMap.image);
    expect((group[0] as MeshBasicMaterial).color.getHex()).toBe(0xffffff);
    expect(source.emissiveMap).toBe(emissiveMap);
  });

  it("does not enable vertex colors for a UV-only photogrammetry primitive", () => {
    const geometry = new BufferGeometry();
    geometry.setAttribute("position", new BufferAttribute(new Float32Array([0, 0, 0]), 3));
    geometry.setAttribute("uv", new BufferAttribute(new Float32Array([0, 0]), 2));
    const mesh = new Mesh(geometry, new MeshStandardMaterial({ emissiveMap: texture() }));

    expect(hasVertexColors(mesh)).toBe(false);
    const painted = paintedMaterial(mesh.material, hasVertexColors(mesh));
    expect(painted.vertexColors).toBe(false);
    expect(painted.map?.image).toBe(mesh.material.emissiveMap?.image);
  });

  it("samples the full-size photograph, since faces packed one by one bleed into each other when shrunk", () => {
    const map = texture();
    map.minFilter = LinearMipmapLinearFilter;
    map.generateMipmaps = true;
    const painted = paintedMaterial(new MeshStandardMaterial({ map }), false);

    expect(painted.map?.minFilter).toBe(LinearFilter);
    expect(painted.map?.generateMipmaps).toBe(false);
    expect(map.minFilter).toBe(LinearMipmapLinearFilter);
    expect(map.generateMipmaps).toBe(true);
  });
});
