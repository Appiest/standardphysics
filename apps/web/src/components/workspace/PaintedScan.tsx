"use client";

import { useGLTF } from "@react-three/drei";
import { useEffect, useMemo } from "react";
import { Mesh, MeshBasicMaterial, type Color, type Material, type Texture } from "three";

type PhotographSourceMaterial = Material & {
  alphaMap?: Texture | null;
  color?: Color;
  emissiveMap?: Texture | null;
  map?: Texture | null;
};

/** Creates an unlit display material without changing the GLTF-owned source material or textures. */
export function paintedMaterial(source: Material, vertexColors: boolean): MeshBasicMaterial {
  const photographic = source as PhotographSourceMaterial;
  const usesEmissiveMap = !photographic.map && Boolean(photographic.emissiveMap);
  return new MeshBasicMaterial({
    alphaMap: photographic.alphaMap ?? null,
    alphaTest: source.alphaTest,
    blending: source.blending,
    color: usesEmissiveMap ? "#ffffff" : photographic.color?.clone() ?? "#ffffff",
    depthTest: source.depthTest,
    depthWrite: source.depthWrite,
    map: photographic.map ?? photographic.emissiveMap ?? null,
    opacity: source.opacity,
    side: source.side,
    transparent: source.transparent,
    vertexColors,
  });
}

export function paintedMaterials(source: Material | Material[], vertexColors: boolean): Material | Material[] {
  return Array.isArray(source) ? source.map((material) => paintedMaterial(material, vertexColors)) : paintedMaterial(source, vertexColors);
}

export function hasVertexColors(mesh: Mesh): boolean {
  const color = mesh.geometry.getAttribute("color");
  return color !== undefined && color.itemSize >= 3;
}

function disposePaintedMaterials(material: Material | Material[]) {
  if (Array.isArray(material)) material.forEach((item) => item.dispose());
  else material.dispose();
}

/**
 * The captured surface with the colour the photos gave it.
 *
 * Colour rides on the vertices, already the brightness the room was, so the
 * material is unlit: lighting it a second time would double the shadows that
 * are in the photographs. Nothing here can be picked or dragged, because the
 * scan is one piece of geometry and the graph is what owns objects.
 */
export function PaintedScan({ url }: { url: string }) {
  const { scene } = useGLTF(url);
  const painted = useMemo(() => {
    const copy = scene.clone(true);
    copy.traverse((object) => {
      if (!(object instanceof Mesh)) return;
      object.material = paintedMaterials(object.material, hasVertexColors(object));
      object.raycast = () => null;
    });
    return copy;
  }, [scene]);
  useEffect(() => () => {
    painted.traverse((object) => {
      if (object instanceof Mesh) disposePaintedMaterials(object.material);
    });
  }, [painted]);
  return <primitive object={painted} />;
}
