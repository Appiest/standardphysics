"use client";

import { Edges, Html, useGLTF } from "@react-three/drei";
import { useThree, type ThreeEvent } from "@react-three/fiber";
import { Lock } from "@phosphor-icons/react";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { BoxGeometry, Matrix4, Mesh, MeshStandardMaterial, Plane, Raycaster, Vector3, type BufferGeometry, type Intersection, type Material } from "three";
import { canUseCapturedGlbGeometry, displayScale, MAX_DISPLAY_WALL_HEIGHT } from "@/lib/display-geometry";
import { groupGlbPrimitives } from "@/lib/glb-parts";
import { displayMatrix, toViewerMatrix } from "@/lib/scene-matrix";
import type { SceneGraph, SceneNode } from "@/types/contracts";
import { MODEL, nodeColor, WALL_CUT_HEIGHT } from "./palette";

const UNIT_BOX = new BoxGeometry(1, 1, 1);
/** Viewer supplies one stable ground plane; RoomPlan floors are often rotated zero-depth shells. */
const HIDDEN_KINDS = new Set<SceneNode["kind"]>(["door", "window", "opening"]);
const WALL_CLIP_PLANE = new Plane(new Vector3(0, -1, 0), WALL_CUT_HEIGHT);
const WALL_CLIP_PLANES = [WALL_CLIP_PLANE];

type Placed = { node: SceneNode; geometry: BufferGeometry; matrix: Matrix4; sourceMaterial: Material | Material[] | null };

export type ArrangeHandlers = {
  activeId: string | null;
  blockedIds: Set<string>;
  onGrab: (nodeId: string) => void;
  onDrag: (nodeId: string, dx: number, dy: number) => void;
  onDrop: (nodeId: string) => void;
};

function boxMatrix(node: SceneNode): Matrix4 {
  const [sx, sy, sz] = displayScale(node);
  const scale = new Matrix4().makeScale(sx, sy, sz);
  const matrix = toViewerMatrix(node.transform);
  if (node.kind === "wall" && node.dimensions.z > MAX_DISPLAY_WALL_HEIGHT) {
    const drop = (node.dimensions.z - MAX_DISPLAY_WALL_HEIGHT) / 2;
    matrix.multiply(new Matrix4().makeTranslation(0, -drop, 0));
  }
  return matrix.multiply(scale);
}

function fallbackPlacement(node: SceneNode): Placed {
  return { node, geometry: UNIT_BOX, matrix: boxMatrix(node), sourceMaterial: null };
}

// eslint-disable-next-line complexity
function placeFromGlb(meshes: Mesh[] | undefined, node: SceneNode, exported: SceneNode | undefined, stale: boolean): Placed[] {
  // RoomPlan floor shells can have a zero axis. Only draw a floor when the GLB
  // supplies real triangles; a unit-box fallback would make a black slab.
  if ((!meshes || !exported) && node.kind === "floor") return [];
  if (!meshes || !exported || stale) return [fallbackPlacement(node)];
  const placed = meshes.flatMap((mesh) => {
    const matrix = displayMatrix(mesh.matrixWorld, exported.transform, node.transform);
    if (!canUseCapturedGlbGeometry(node, mesh.geometry, matrix, false)) return [];
    return [{ node, geometry: mesh.geometry, matrix, sourceMaterial: mesh.material }];
  });
  if (placed.length > 0) return placed;
  return node.kind === "floor" ? [] : [fallbackPlacement(node)];
}

function useGlbMeshes(url: string, nodeIds: Set<string>): Map<string, Mesh[]> {
  const { scene } = useGLTF(url);
  return useMemo(() => {
    scene.updateMatrixWorld(true);
    return groupGlbPrimitives(scene, nodeIds);
  }, [scene, nodeIds]);
}

type ModelProps = {
  shown: SceneGraph;
  focus: Set<string> | null;
  focusColor: string;
  onSelectNode: (nodeId: string) => void;
  arrange: ArrangeHandlers | null;
  dragAllNodes?: boolean;
  lightweight?: boolean;
  cutWalls?: boolean;
  materialMode?: "reconstructed" | "captured" | "plain" | "coverage";
  staleNodeIds?: Set<string>;
  coverage?: Map<string, number>;
  /** Keep SceneGraph geometry interactive without drawing the measured bounds. */
  pickOnly?: boolean;
  /** The owner is choosing a piece: every piece stays visible and tappable, and the focus only outlines. */
  picking?: boolean;
};

const SCRATCH_HIT = new Vector3();

/** A level plane at the height the piece was grabbed, so it stays under the pointer instead of the floor below it. */
function grabPlane(event: ThreeEvent<PointerEvent>): Plane {
  return new Plane(new Vector3(0, 1, 0), -event.point.y);
}

function planeHit(event: ThreeEvent<PointerEvent>, plane: Plane, target: Vector3 = SCRATCH_HIT): Vector3 | null {
  return event.ray.intersectPlane(plane, target);
}

function setCursor(cursor: string) {
  document.body.style.cursor = cursor;
}

function useDrag(node: SceneNode, arrange: ArrangeHandlers | null, dragAllNodes: boolean | undefined) {
  const from = useRef<Vector3 | null>(null);
  const plane = useRef<Plane | null>(null);
  const pendingDelta = useRef<{ dx: number; dy: number }>({ dx: 0, dy: 0 });
  const rafId = useRef<number | null>(null);
  const arrangeRef = useRef(arrange);
  useEffect(() => {
    arrangeRef.current = arrange;
  }, [arrange]);

  useEffect(() => {
    return () => {
      if (rafId.current !== null) cancelAnimationFrame(rafId.current);
    };
  }, []);

  if (!arrange || (!dragAllNodes && (!node.movable || node.kind !== "object"))) return {};

  return {
    onPointerDown(event: ThreeEvent<PointerEvent>) {
      event.stopPropagation();
      (event.target as unknown as Element).setPointerCapture(event.pointerId);
      plane.current = grabPlane(event);
      from.current = planeHit(event, plane.current, new Vector3());
      pendingDelta.current.dx = 0;
      pendingDelta.current.dy = 0;
      arrange.onGrab(node.id);
      setCursor("grabbing");
    },
    onPointerMove(event: ThreeEvent<PointerEvent>) {
      if (!from.current || !plane.current) return;
      const hit = planeHit(event, plane.current, SCRATCH_HIT);
      if (!hit) return;
      const dx = hit.x - from.current.x;
      const dy = -(hit.z - from.current.z);
      from.current.copy(hit);
      pendingDelta.current.dx += dx;
      pendingDelta.current.dy += dy;

      if (rafId.current === null) {
        rafId.current = requestAnimationFrame(() => {
          rafId.current = null;
          const currentDx = pendingDelta.current.dx;
          const currentDy = pendingDelta.current.dy;
          if (currentDx !== 0 || currentDy !== 0) {
            pendingDelta.current.dx = 0;
            pendingDelta.current.dy = 0;
            arrangeRef.current?.onDrag(node.id, currentDx, currentDy);
          }
        });
      }
    },
    onPointerUp(event: ThreeEvent<PointerEvent>) {
      if (!from.current) return;
      (event.target as unknown as Element).releasePointerCapture(event.pointerId);
      from.current = null;
      if (rafId.current !== null) {
        cancelAnimationFrame(rafId.current);
        rafId.current = null;
      }
      const { dx, dy } = pendingDelta.current;
      if (dx !== 0 || dy !== 0) {
        pendingDelta.current.dx = 0;
        pendingDelta.current.dy = 0;
        arrangeRef.current?.onDrag(node.id, dx, dy);
      }
      arrangeRef.current?.onDrop(node.id);
      setCursor("grab");
    },
  };
}

function edgeColor(node: SceneNode, props: Omit<ModelProps, "shown">): string | null {
  if (props.arrange?.blockedIds.has(node.id)) return MODEL.problem;
  if (props.arrange?.activeId === node.id) return MODEL.accent;
  if (props.focus?.has(node.id)) return props.focusColor;
  return null;
}

function LockMark({ node }: { node: SceneNode }) {
  const top = new Vector3(node.transform.m[3], node.transform.m[11] + node.dimensions.z / 2 + 0.15, -node.transform.m[7]);
  return (
    <Html position={top} center style={{ pointerEvents: "none" }}>
      <span className="grid size-7 place-items-center rounded-full bg-ink text-paper shadow-md">
        <Lock size={14} weight="bold" aria-label="Fixed in place" />
      </span>
    </Html>
  );
}

function nodeState(node: SceneNode, props: Omit<ModelProps, "shown">) {
  const inFocus = props.picking || props.focus === null || props.focus.has(node.id);
  return {
    faded: !inFocus,
    outline: edgeColor(node, props),
    lockable: props.arrange !== null && !props.dragAllNodes && node.kind === "object" && !node.movable,
    selectable: inFocus && props.arrange === null,
  };
}

function styledMaterial(source: Material | Material[], faded: boolean, clippingPlanes: Plane[] | null, stripPhotoMap: boolean): Material | Material[] {
  // eslint-disable-next-line complexity
  const style = (material: Material) => {
    const copy = material.clone();
    if (stripPhotoMap && copy instanceof MeshStandardMaterial) copy.map = null;
    copy.transparent = faded || material.transparent;
    copy.opacity = faded ? material.opacity * 0.15 : material.opacity;
    copy.depthWrite = faded ? false : material.depthWrite;
    copy.clippingPlanes = clippingPlanes?.map((plane) => plane.clone()) ?? material.clippingPlanes?.map((plane) => plane.clone()) ?? null;
    copy.clipShadows = Boolean(clippingPlanes && clippingPlanes.length > 0);
    return copy;
  };
  return Array.isArray(source) ? source.map(style) : style(source);
}

function useSourceMaterial(source: Material | Material[] | null, faded: boolean, clipWall: boolean, stripPhotoMap: boolean) {
  const material = useMemo(
    () => source ? styledMaterial(source, faded, clipWall ? WALL_CLIP_PLANES : null, stripPhotoMap) : null,
    [source, faded, clipWall, stripPhotoMap],
  );
  useEffect(() => () => {
    if (Array.isArray(material)) material.forEach((item) => item.dispose());
    else material?.dispose();
  }, [material]);
  return material;
}

function coverageColor(fraction: number): string {
  if (fraction >= 0.8) return "#2f8f5b";
  if (fraction >= 0.4) return "#c99a32";
  return "#bd5252";
}

// eslint-disable-next-line complexity
function DisplayMaterial({ source, node, faded, clipWall, mode = "plain", stale = false, coverage, pickOnly = false }: { source: Material | Material[] | null; node: SceneNode; faded: boolean; clipWall: boolean; mode?: ModelProps["materialMode"]; stale?: boolean; coverage?: number; pickOnly?: boolean }) {
  const canUseSource = mode !== "coverage" && !stale;
  const material = useSourceMaterial(!pickOnly && canUseSource ? source : null, faded, clipWall, mode === "plain");
  if (pickOnly) return <meshBasicMaterial colorWrite={false} depthWrite={false} />;
  if (material) return <primitive attach="material" object={material} />;
  return <meshStandardMaterial
    color={mode === "coverage" ? coverageColor(coverage ?? 0) : nodeColor(node)}
    roughness={0.92}
    transparent={faded}
    opacity={faded ? 0.15 : 1}
    depthWrite={!faded}
    clippingPlanes={clipWall ? WALL_CLIP_PLANES : null}
    clipShadows={clipWall}
  />;
}

/** The renderer clips pixels, but Three's default raycast still sees them. */
function clippedWallRaycast(this: Mesh, raycaster: Raycaster, intersections: Intersection[]) {
  const start = intersections.length;
  Mesh.prototype.raycast.call(this, raycaster, intersections);
  for (let index = intersections.length - 1; index >= start; index -= 1) {
    if (intersections[index].point.y > WALL_CUT_HEIGHT) intersections.splice(index, 1);
  }
}

function clipsWall(node: SceneNode, cutWalls: boolean | undefined): boolean {
  return node.kind === "wall" && (cutWalls ?? true);
}

function meshRaycast(faded: boolean, clipWall: boolean) {
  if (faded) return () => null;
  return clipWall ? clippedWallRaycast : undefined;
}

type ModelNodeProps = { placed: Placed } & Omit<ModelProps, "shown">;

// eslint-disable-next-line complexity
function areModelNodePropsEqual(prev: ModelNodeProps, next: ModelNodeProps): boolean {
  if (prev.placed !== next.placed) return false;
  if (prev.cutWalls !== next.cutWalls) return false;
  if (prev.lightweight !== next.lightweight) return false;
  if (prev.dragAllNodes !== next.dragAllNodes) return false;
  if (prev.materialMode !== next.materialMode) return false;
  if (prev.pickOnly !== next.pickOnly) return false;
  if (prev.picking !== next.picking) return false;
  if (prev.onSelectNode !== next.onSelectNode) return false;
  if (prev.focusColor !== next.focusColor) return false;

  const nodeId = prev.placed.node.id;

  const prevFaded = prev.focus !== null && !prev.focus.has(nodeId);
  const nextFaded = next.focus !== null && !next.focus.has(nodeId);
  if (prevFaded !== nextFaded) return false;

  const prevFocused = prev.focus?.has(nodeId) ?? false;
  const nextFocused = next.focus?.has(nodeId) ?? false;
  if (prevFocused !== nextFocused) return false;

  const prevStale = prev.staleNodeIds?.has(nodeId) ?? false;
  const nextStale = next.staleNodeIds?.has(nodeId) ?? false;
  if (prevStale !== nextStale) return false;

  const prevCoverage = prev.coverage?.get(nodeId);
  const nextCoverage = next.coverage?.get(nodeId);
  if (prevCoverage !== nextCoverage) return false;

  const prevActive = prev.arrange?.activeId === nodeId;
  const nextActive = next.arrange?.activeId === nodeId;
  if (prevActive !== nextActive) return false;

  const prevBlocked = prev.arrange?.blockedIds.has(nodeId) ?? false;
  const nextBlocked = next.arrange?.blockedIds.has(nodeId) ?? false;
  if (prevBlocked !== nextBlocked) return false;

  const prevArrangeEnabled = prev.arrange !== null;
  const nextArrangeEnabled = next.arrange !== null;
  if (prevArrangeEnabled !== nextArrangeEnabled) return false;

  return true;
}

// eslint-disable-next-line complexity
const ModelNode = memo(function ModelNode({ placed, ...props }: ModelNodeProps) {
  const { node, geometry, matrix } = placed;
  const [hovered, setHovered] = useState(false);
  const { faded, outline, lockable, selectable } = nodeState(node, props);
  const drag = useDrag(node, props.arrange, props.dragAllNodes);
  const draggable = "onPointerDown" in drag;
  const clipWall = clipsWall(node, props.cutWalls);

  function select(event: ThreeEvent<MouseEvent>) {
    event.stopPropagation();
    props.onSelectNode(node.id);
  }

  function hover(on: boolean) {
    setHovered(on);
    if (draggable) setCursor(on ? "grab" : "auto");
  }

  return (
    <mesh
      geometry={geometry}
      matrix={matrix}
      matrixAutoUpdate={false}
      castShadow={!props.pickOnly && !props.lightweight && !faded && node.kind !== "floor"}
      receiveShadow={!props.pickOnly && !props.lightweight}
      onClick={selectable ? select : undefined}
      onPointerOver={() => hover(true)}
      onPointerOut={() => hover(false)}
      raycast={meshRaycast(faded, clipWall)}
      name={node.id}
      {...drag}
    >
      <DisplayMaterial source={placed.sourceMaterial} node={node} faded={faded} clipWall={clipWall} mode={props.materialMode} stale={props.staleNodeIds?.has(node.id)} coverage={props.coverage?.get(node.id)} pickOnly={props.pickOnly} />
      {outline && !props.pickOnly && !props.lightweight && <Edges threshold={20} lineWidth={3} color={outline} renderOrder={5} clippingPlanes={clipWall ? WALL_CLIP_PLANES : null} />}
      {lockable && hovered && !props.pickOnly && <LockMark node={node} />}
    </mesh>
  );
}, areModelNodePropsEqual);

function ModelNodes({ placements, ...props }: { placements: Placed[] } & Omit<ModelProps, "shown">) {
  const invalidate = useThree((state) => state.invalidate);
  useEffect(() => invalidate(), [placements, props.focus, props.arrange, invalidate]);
  return (
    <group>
      {placements.map((placed, index) => (
        <ModelNode key={`${placed.node.id}-${index}`} placed={placed} {...props} />
      ))}
    </group>
  );
}

function visibleNodes(scene: SceneGraph, includeFloors = false): SceneNode[] {
  return scene.nodes.filter((node) => !HIDDEN_KINDS.has(node.kind) && (includeFloors || node.kind !== "floor"));
}

const glbPlacementCache = new WeakMap<SceneNode, { stale: boolean; placed: Placed[] }>();
const boxPlacementCache = new WeakMap<SceneNode, Placed>();

export function GlbShopModel({ url, exported, ...props }: ModelProps & { url: string; exported: SceneGraph }) {
  const nodeIds = useMemo(() => new Set(exported.nodes.map((node) => node.id)), [exported.nodes]);
  const meshes = useGlbMeshes(url, nodeIds);

  const placements = useMemo(() => {
    const exportedById = new Map(exported.nodes.map((node) => [node.id, node]));
    return visibleNodes(props.shown, true).flatMap((node) => {
      const stale = props.staleNodeIds?.has(node.id) ?? false;
      const cached = glbPlacementCache.get(node);
      if (cached && cached.stale === stale) {
        return cached.placed;
      }
      const fresh = placeFromGlb(meshes.get(node.id), node, exportedById.get(node.id), stale);
      glbPlacementCache.set(node, { stale, placed: fresh });
      return fresh;
    });
  }, [meshes, exported, props.shown, props.staleNodeIds]);

  return <ModelNodes placements={placements} {...props} />;
}

export function BoxShopModel(props: ModelProps) {
  const placements = useMemo(
    () =>
      visibleNodes(props.shown).map((node) => {
        const cached = boxPlacementCache.get(node);
        if (cached) return cached;
        const fresh: Placed = { node, geometry: UNIT_BOX, matrix: boxMatrix(node), sourceMaterial: null };
        boxPlacementCache.set(node, fresh);
        return fresh;
      }),
    [props.shown],
  );

  return <ModelNodes placements={placements} {...props} />;
}
