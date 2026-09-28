"use client";

import { useGLTF } from "@react-three/drei";
import { useThree } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { BackSide, BufferGeometry, Group, LinearFilter, Matrix4, Mesh, MeshBasicMaterial, Plane, Vector3, type Color, type Material, type Object3D, type Texture } from "three";
import { carveGeometry, carveRegion, type CarveRegion } from "@/lib/carve-scan";
import { toViewerMatrix } from "@/lib/scene-matrix";
import { MoveMarks } from "./MoveMarks";
import type { SceneGraph, SceneNode } from "@/types/contracts";

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

const NOT_PICKABLE = () => null;

/** The pieces the owner may move, cut out of the scan so each one can follow its own box; null leaves the scan whole. */
export type ScanPieces = { carve: SceneNode[]; placed: SceneGraph };

/** The painted room and its cut-out pieces, with what carving made so it can be released with them. */
type Painted = { room: Object3D; pieces: Map<string, Group>; made: { geometries: BufferGeometry[]; materials: Material[] } };

/**
 * The captured surface with the colour the photos gave it.
 *
 * Colour rides on the vertices, already the brightness the room was, so the
 * material is unlit: lighting it a second time would double the shadows that
 * are in the photographs. The scan is one piece of geometry and the graph is
 * what owns objects, so nothing here can be picked or dragged. While a layout
 * is being planned, each movable piece's triangles are cut out and drawn where
 * its box now stands, so moving the counter moves the scanned counter.
 */
export function PaintedScan({ url, cutAbove = null, pieces = null }: { url: string; cutAbove?: number | null; pieces?: ScanPieces | null }) {
  const { scene } = useGLTF(url);
  const carve = pieces?.carve ?? null;
  const painted = useMemo(() => {
    const made = paint(scene, carve);
    cutAt(made.room, cutAbove);
    return made;
  }, [scene, carve, cutAbove]);
  useEffect(() => () => disposePainted(painted), [painted]);
  return (
    <>
      <primitive object={painted.room} />
      {pieces && carve?.map((node) => {
        const group = painted.pieces.get(node.id);
        const now = pieces.placed.nodes.find((candidate) => candidate.id === node.id) ?? node;
        return group ? <MovedPiece key={node.id} group={group} from={node} to={now} /> : null;
      })}
    </>
  );
}

function paint(scene: Object3D, carve: SceneNode[] | null): Painted {
  const room = scene.clone(true);
  room.updateMatrixWorld(true);
  const regions = carve?.map(carveRegion) ?? [];
  const painted: Painted = { room, pieces: new Map(), made: { geometries: [], materials: [] } };
  const meshes: Mesh[] = [];
  room.traverse((object) => { if (object instanceof Mesh) meshes.push(object); });
  for (const mesh of meshes) {
    mesh.material = paintedMaterials(mesh.material, hasVertexColors(mesh));
    mesh.raycast = NOT_PICKABLE;
    if (regions.length > 0) carveInto(mesh, regions, painted);
    mesh.add(backfaceShell(mesh));
  }
  return painted;
}

/** Takes each piece's triangles out of the mesh and hangs them, still where they were scanned, on that piece's group. */
function carveInto(mesh: Mesh, regions: CarveRegion[], { pieces, made }: Painted) {
  const carved = carveGeometry(mesh.geometry, mesh.matrixWorld, regions);
  mesh.geometry = carved.room ?? new BufferGeometry();
  made.geometries.push(mesh.geometry);
  for (const [id, geometry] of carved.pieces) {
    const part = new Mesh(geometry, mesh.material);
    part.matrixAutoUpdate = false;
    part.matrix.copy(mesh.matrixWorld);
    part.raycast = NOT_PICKABLE;
    const shell = backfaceShell(part);
    part.add(shell);
    pieceGroup(pieces, id).add(part);
    made.geometries.push(geometry);
    made.materials.push(shell.material as Material);
  }
}

function pieceGroup(pieces: Map<string, Group>, id: string): Group {
  const existing = pieces.get(id);
  if (existing) return existing;
  const group = new Group();
  pieces.set(id, group);
  return group;
}

/** The rigid move from where a piece was scanned to where its box stands now. */
function moveBetween(from: SceneNode, to: SceneNode): Matrix4 {
  return toViewerMatrix(to.transform).multiply(toViewerMatrix(from.transform).invert());
}

function hasMoved(from: SceneNode, to: SceneNode): boolean {
  return from.transform.m.some((value, index) => Math.abs(value - to.transform.m[index]) > 1e-4);
}

/**
 * A scanned piece drawn where its box now stands, with marks on the floor for
 * where it was, where it went and how it got there.
 */
function MovedPiece({ group, from, to }: { group: Group; from: SceneNode; to: SceneNode }) {
  const invalidate = useThree((state) => state.invalidate);
  const mover = useRef<Group>(null);
  useLayoutEffect(() => {
    if (!mover.current) return;
    mover.current.matrix.copy(moveBetween(from, to));
    mover.current.matrixWorldNeedsUpdate = true;
    invalidate();
  }, [from, to, invalidate]);
  return (
    <>
      <group ref={mover} matrixAutoUpdate={false}><primitive object={group} /></group>
      {hasMoved(from, to) && <MoveMarks from={from} to={to} />}
    </>
  );
}

function disposePainted({ room, made }: Painted) {
  room.traverse((object) => {
    if (object instanceof Mesh) disposePaintedMaterials(object.material);
  });
  made.geometries.forEach((geometry) => geometry.dispose());
  made.materials.forEach((material) => material.dispose());
}

/** Everything above `height` left out, so a view from above looks into the rooms rather than onto a ceiling. */
function cutAt(root: Object3D, height: number | null) {
  const planes = height === null ? null : [new Plane(new Vector3(0, -1, 0), height)];
  root.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) material.clippingPlanes = planes;
  });
}
