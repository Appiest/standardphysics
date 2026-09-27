"use client";

import { useGLTF } from "@react-three/drei";
import { useEffect, useMemo } from "react";
import { BackSide, LinearFilter, Mesh, MeshBasicMaterial, Plane, Vector3, type Color, type Material, type Object3D, type Texture } from "three";

/** The colour of a surface seen from the side the phone never stood on. */
const UNMEASURED = "#8d8880";

type PhotographSourceMaterial = Material & {
  alphaMap?: Texture | null;
  color?: Color;
  emissiveMap?: Texture | null;
  map?: Texture | null;
};

/**
 * The photograph with no smaller copies of itself to fall back on.
 *
 * The scan's atlas packs every face as its own small island, so the half- and
 * quarter-size copies a GPU samples from a distance blend each island into its
 * neighbours, and a floor seen from across the room was crossed with light
 * lines. Sampling the full-size photograph keeps each face's own pixels. The
 * copy shares the image, so the GLTF's texture is left as it was.
 */
export function withoutMipmaps(texture: Texture | null): Texture | null {
  if (!texture) return null;
  const copy = texture.clone();
  copy.minFilter = LinearFilter;
  copy.generateMipmaps = false;
  copy.needsUpdate = true;
  copy.userData = { ...copy.userData, ownedByPaintedScan: true };
  return copy;
}

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
    map: withoutMipmaps(photographic.map ?? photographic.emissiveMap ?? null),
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

function disposePaintedMaterial(material: Material) {
  const map = (material as MeshBasicMaterial).map;
  if (map?.userData.ownedByPaintedScan) map.dispose();
  material.dispose();
}

function disposePaintedMaterials(material: Material | Material[]) {
  if (Array.isArray(material)) material.forEach(disposePaintedMaterial);
  else disposePaintedMaterial(material);
}

/**
 * A second pass that closes the scan from behind.
 *
 * A phone walked past a desk measures its top and never the underside, so the
 * scan holds a real surface with nothing below it. Drawn from the front only,
 * that surface vanishes when the camera drops beneath it, and a room read as
 * scattered fragments hanging in the air: a table appeared to float because
 * its legs were not there to hold it up, and the top itself disappeared the
 * moment you looked up at it.
 *
 * Backfaces are drawn in one flat unlit colour instead. The scan then reads as
 * a shell with holes in it, and every hole is the shape of somewhere the phone
 * was never pointed. Nothing is filled in and no surface is moved; the blank
 * grey is the absence of a measurement, which is what it looks like.
 */
function unmeasuredSide(source: Material): MeshBasicMaterial {
  return new MeshBasicMaterial({
    color: UNMEASURED,
    depthTest: source.depthTest,
    depthWrite: true,
    side: BackSide,
  });
}

function backfaceShell(mesh: Mesh): Mesh {
  const shell = new Mesh(mesh.geometry, unmeasuredSide(asOne(mesh.material)));
  shell.raycast = () => null;
  shell.renderOrder = -1;
  return shell;
}

function asOne(material: Material | Material[]): Material {
  return Array.isArray(material) ? material[0] : material;
}

/**
 * The captured surface with the colour the photos gave it.
 *
 * Colour rides on the vertices, already the brightness the room was, so the
 * material is unlit: lighting it a second time would double the shadows that
 * are in the photographs. Nothing here can be picked or dragged, because the
 * scan is one piece of geometry and the graph is what owns objects.
 */
export function PaintedScan({ url, cutAbove = null }: { url: string; cutAbove?: number | null }) {
  const { scene } = useGLTF(url);
  const painted = useMemo(() => {
    const copy = scene.clone(true);
    const shells: { parent: Mesh; shell: Mesh }[] = [];
    copy.traverse((object) => {
      if (!(object instanceof Mesh)) return;
      object.material = paintedMaterials(object.material, hasVertexColors(object));
      object.raycast = () => null;
      shells.push({ parent: object, shell: backfaceShell(object) });
    });
    for (const { parent, shell } of shells) parent.add(shell);
    cutAt(copy, cutAbove);
    return copy;
  }, [scene, cutAbove]);
  useEffect(() => () => {
    painted.traverse((object) => {
      if (object instanceof Mesh) disposePaintedMaterials(object.material);
    });
  }, [painted]);
  return <primitive object={painted} />;
}

/** Everything above `height` left out, so a view from above looks into the rooms rather than onto a ceiling. */
function cutAt(root: Object3D, height: number | null) {
  const planes = height === null ? null : [new Plane(new Vector3(0, -1, 0), height)];
  root.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) material.clippingPlanes = planes;
  });
}
