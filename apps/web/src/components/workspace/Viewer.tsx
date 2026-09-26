"use client";

import { Canvas } from "@react-three/fiber";
import { Component, Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import { clipPlanes, zoomRange, type ViewerPose } from "@/lib/camera";
import type { Focus } from "@/lib/findings";
import type { NodeTextureCoverage, SceneGraph, SceneNode } from "@/types/contracts";
import { FindingAnnotation } from "./Annotation";
import { CameraRig } from "./CameraRig";
import { MODEL, outcomeColor, SCAN_CUT_HEIGHT } from "./palette";
import { type ArrangeHandlers, BoxShopModel, GlbShopModel } from "./ShopModel";
import { LidarShopModel } from "./LidarShopModel";
import { CombinedRooms } from "./CombinedRooms";
import { PaintedScan } from "./PaintedScan";
import { GaussianSplatScan } from "./GaussianSplatScan";
import type { CapturedSplatAsset } from "@/lib/captured-splats";
import type { RoomGroup, RoomPlacement } from "@/lib/room-groups";
import { showsSplats } from "@/lib/viewer-source";
import type { MotionPoint, WheelchairProfile } from "@/lib/wheelchair-motion";
import { type RouteHandles, StopMarkers } from "./StopMarkers";
import { WheelchairController, type WheelchairState } from "./WheelchairController";

type ViewerProps = {
  scene: SceneGraph;
  highlightNodeIds?: string[] | null;
  /** Every piece stays visible and tappable while one is outlined, for choosing a piece. */
  picking?: boolean;
  /** The walks of a combined scan and where they have been dragged, drawn as captured surface. */
  combinedRooms?: { rooms: RoomGroup[]; placements: Record<string, RoomPlacement> } | null;
  exported: SceneGraph;
  arrange: ArrangeHandlers | null;
  dragAllNodes?: boolean;
  lightweight?: boolean;
  route: RouteHandles | null;
  dragging: boolean;
  cutWalls: boolean;
  glbUrl: string | null;
  lidarUrl: string | null;
  pose: ViewerPose;
  selected: Focus | null;
  onSelectNode: (nodeId: string) => void;
  onClearSelection: () => void;
  materialMode: "reconstructed" | "captured" | "plain" | "coverage" | "scan" | "splat";
  scanGlbUrl: string | null;
  splatAssets?: CapturedSplatAsset[];
  onSplatError?: (message: string | null) => void;
  staleNodeIds: string[];
  coverage: NodeTextureCoverage[];
  wheelchairMode?: boolean;
  wheelchairProfile?: WheelchairProfile;
  onWheelchairStateChange?: (state: WheelchairState) => void;
  wheelchairDockTarget?: SceneNode | null;
  wheelchairDockDestination?: MotionPoint | null;
  onClearWheelchairDock?: () => void;
  onWheelchairSelectNode?: (node: SceneNode) => void;
  onWheelchairExit?: () => void;
  /** Where the walk-through starts and which way it faces. Left out, it starts in a corner facing -z. */
  wheelchairStart?: WheelchairStart | null;
};

export type WheelchairStart = { position: [number, number, number]; yaw: number };


class GlbFallback extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

const GROUND_PLANE_ARGS: [number, number] = [160, 160];
const HEMI_LIGHT_ARGS: [string, string, number] = ["#ffffff", "#d8d2c4", 1.25];
const BG_COLOR_ARGS: [string] = ["#f6f5f1"];
const DPR_DEFAULT: [number, number] = [1, 2];
const DPR_LIGHTWEIGHT: [number, number] = [1, 1];
const DPR_SPLATS: [number, number] = [1, 1.5];
const EMPTY_STALE_SET = new Set<string>();
const NOTHING_TO_DO = () => {};

/**
 * The light casts only while the renderer draws shadows. Turning the canvas's
 * shadows off stops the shadow map updating but leaves every material sampling
 * the last one drawn, so shadows froze in place while rooms moved. A light that
 * stops casting changes the lighting setup, and three rebuilds the materials
 * without the shadow lookup.
 */
function Lights({ castShadow }: { castShadow: boolean }) {
  return (
    <>
      <hemisphereLight args={HEMI_LIGHT_ARGS} />
      <directionalLight
        position={[6, 22, 10]}
        intensity={1.25}
        castShadow={castShadow}
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-36}
        shadow-camera-right={36}
        shadow-camera-top={36}
        shadow-camera-bottom={-36}
        shadow-bias={-0.0004}
      />
    </>
  );
}

type ShopSurfacesProps = Pick<ViewerProps, "scene" | "exported" | "arrange" | "dragAllNodes" | "lightweight" | "glbUrl" | "scanGlbUrl" | "splatAssets" | "onSplatError" | "lidarUrl" | "selected" | "onSelectNode" | "cutWalls" | "materialMode" | "staleNodeIds" | "coverage" | "highlightNodeIds" | "combinedRooms" | "picking">;

/** The boxes have no captured surface to show, so the captured modes fall back to plain material on them. */
function boxMaterialMode(mode: ViewerProps["materialMode"]) {
  return mode === "scan" || mode === "splat" ? ("plain" as const) : mode;
}

/** The captured room for the modes that show one, or null for the modes that show the boxes. */
function capturedRoom(props: ShopSurfacesProps, boxes: ReactNode, picking: ReactNode): ReactNode | null {
  const { materialMode, scanGlbUrl, splatAssets } = props;
  const splatsOnScreen = showsSplats({ materialMode, hasSplats: Boolean(splatAssets?.length), hasScanGlb: scanGlbUrl !== null });
  if (splatsOnScreen && splatAssets) {
    return <SplatRoom key={JSON.stringify(splatAssets)} assets={splatAssets} fallback={boxes} picking={picking} onError={props.onSplatError} />;
  }
  if (materialMode === "scan" && scanGlbUrl) return <ScannedRoom url={scanGlbUrl} whileLoading={boxes} cutAbove={props.cutWalls ? SCAN_CUT_HEIGHT : null} />;
  return null;
}

function ShopSurfaces(props: ShopSurfacesProps) {
  const { exported, glbUrl, lidarUrl, materialMode, selected, staleNodeIds, coverage, scene, arrange, dragAllNodes, lightweight, onSelectNode, cutWalls, highlightNodeIds, picking: choosing } = props;

  /* Picking a walk in the Combine panel has to show which one it is, or four
     grey floor plans look alike and the one being dragged is anybody's guess.
     The highlight outranks a selected finding because while rooms are being
     placed, that is what the owner is working on. */
  const focus = useMemo(() => {
    if (highlightNodeIds) return new Set<string>(highlightNodeIds);
    return selected?.locus ? new Set<string>(selected.locus.node_ids) : null;
  }, [highlightNodeIds, selected]);
  const staleSet = useMemo(() => (staleNodeIds ? new Set(staleNodeIds) : EMPTY_STALE_SET), [staleNodeIds]);
  const coverageMap = useMemo(() => new Map(coverage.map((entry) => [entry.node_id, entry.textured_fraction])), [coverage]);

  const modelProps = useMemo(() => ({
    shown: scene,
    focus,
    focusColor: highlightNodeIds || !selected ? MODEL.accent : outcomeColor(selected.outcome),
    onSelectNode,
    arrange,
    dragAllNodes,
    lightweight,
    cutWalls,
    materialMode: boxMaterialMode(materialMode),
    staleNodeIds: staleSet,
    coverage: coverageMap,
    picking: choosing,
  }), [scene, focus, selected, highlightNodeIds, onSelectNode, arrange, dragAllNodes, lightweight, cutWalls, materialMode, staleSet, coverageMap, choosing]);

  const boxes = <BoxShopModel {...modelProps} />;
  const picking = <BoxShopModel {...modelProps} pickOnly />;
  if (props.combinedRooms?.rooms.some((room) => room.scan_glb_url)) {
    return (
      <>
        {boxes}
        <CombinedRooms rooms={props.combinedRooms.rooms} placements={props.combinedRooms.placements} />
      </>
    );
  }
  const captured = capturedRoom(props, boxes, picking);
  if (captured) return captured;
  const reconstructed = !glbUrl ? boxes : (
    <GlbFallback key={glbUrl} fallback={boxes}>
      <Suspense fallback={boxes}>
        <GlbShopModel url={glbUrl} exported={exported} {...modelProps} />
      </Suspense>
    </GlbFallback>
  );
  return <group>{lidarUrl && <LidarShopModel key={lidarUrl} url={lidarUrl} />}{reconstructed}</group>;
}

function SplatRoom({ assets, fallback, picking, onError }: { assets: CapturedSplatAsset[]; fallback: ReactNode; picking: ReactNode; onError?: (message: string | null) => void }) {
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => { onError?.(null); }, [onError]);
  return <group>
    {(!ready || failed) && fallback}
    {ready && !failed && picking}
    {!failed && <GaussianSplatScan assets={assets} onReady={() => { setReady(true); onError?.(null); }} onError={() => {
      setFailed(true);
      onError?.("Photographic reconstruction couldn’t load on this device.");
    }} />}
  </group>;
}

/**
 * The room as it was scanned, shown instead of the boxes rather than with them.
 *
 * Two surfaces a few centimetres apart fight over every pixel, and the boxes
 * miss the real surfaces by inches, so drawing both at once gives a room that
 * flickers. The boxes stand in only while the scan is on its way.
 */
function ScannedRoom({ url, whileLoading, cutAbove }: { url: string; whileLoading: ReactNode; cutAbove: number | null }) {
  return (
    <group>
      <GlbFallback key={url} fallback={whileLoading}>
        <Suspense fallback={whileLoading}><PaintedScan url={url} cutAbove={cutAbove} /></Suspense>
      </GlbFallback>
    </group>
  );
}

/** Splats are expensive enough to pay for with resolution and antialiasing; the boxes are not. */
function canvasTuning(lightweight: boolean | undefined, splatAssets: CapturedSplatAsset[] | undefined) {
  const splatting = Boolean(splatAssets?.length);
  return {
    dpr: lightweight ? DPR_LIGHTWEIGHT : splatting ? DPR_SPLATS : DPR_DEFAULT,
    antialias: !splatting,
  };
}

type WheelchairProps = Pick<ViewerProps, "scene" | "wheelchairMode" | "wheelchairProfile" | "onWheelchairStateChange" | "wheelchairDockTarget" | "wheelchairDockDestination" | "onClearWheelchairDock" | "onWheelchairSelectNode" | "onWheelchairExit" | "wheelchairStart">;

function startProps(start: WheelchairStart | null | undefined) {
  return start ? { initialPosition: start.position, initialYaw: start.yaw } : {};
}

function Wheelchair({ scene, wheelchairMode, wheelchairProfile, onWheelchairStateChange, wheelchairDockTarget, wheelchairDockDestination, onClearWheelchairDock, onWheelchairSelectNode, onWheelchairExit, wheelchairStart }: WheelchairProps) {
  if (!wheelchairMode || !wheelchairProfile || !onWheelchairStateChange) return null;
  return (
    <WheelchairController
      active={wheelchairMode}
      scene={scene}
      profile={wheelchairProfile}
      onStateChange={onWheelchairStateChange}
      dockTarget={wheelchairDockTarget ?? null}
      dockDestination={wheelchairDockDestination ?? null}
      onClearDock={onClearWheelchairDock ?? NOTHING_TO_DO}
      onSelectNode={onWheelchairSelectNode ?? NOTHING_TO_DO}
      onExit={onWheelchairExit}
      {...startProps(wheelchairStart)}
    />
  );
}

export default function Viewer(viewerProps: ViewerProps) {
  const {
    scene,
    lightweight,
    route,
    dragging,
    splatAssets,
    pose,
    selected,
    onClearSelection,
    wheelchairMode = false,
    wheelchairProfile,
    onWheelchairStateChange,
    wheelchairDockTarget = null,
    wheelchairDockDestination = null,
    onClearWheelchairDock,
    onWheelchairSelectNode,
    onWheelchairExit,
  } = viewerProps;
  const tuning = canvasTuning(lightweight, splatAssets);
  return (
    <Canvas
      frameloop={wheelchairMode ? "always" : "demand"}
      dpr={tuning.dpr}
      shadows={!lightweight}
      camera={{ position: pose.position, fov: pose.fov, ...clipPlanes(scene) }}
      flat
      gl={{ antialias: tuning.antialias, localClippingEnabled: true }}
      onCreated={(state) => {
        if (process.env.NODE_ENV === "development") Object.assign(window, { __viewer: state });
        state.camera.lookAt(pose.target[0], pose.target[1], pose.target[2]);
      }}
      onPointerMissed={onClearSelection}
      aria-label="3D model of the shop"
    >
      <color attach="background" args={BG_COLOR_ARGS} />
      <Lights castShadow={!lightweight} />
      {!wheelchairMode && <CameraRig pose={pose} locked={dragging} bounds={null} zoom={zoomRange(scene)} />}
      <Wheelchair
        scene={scene}
        wheelchairMode={wheelchairMode}
        wheelchairProfile={wheelchairProfile}
        onWheelchairStateChange={onWheelchairStateChange}
        wheelchairDockTarget={wheelchairDockTarget}
        wheelchairDockDestination={wheelchairDockDestination}
        onClearWheelchairDock={onClearWheelchairDock}
        onWheelchairSelectNode={onWheelchairSelectNode}
        onWheelchairExit={onWheelchairExit}
        wheelchairStart={viewerProps.wheelchairStart}
      />
      <mesh rotation-x={-Math.PI / 2} position-y={-0.002} receiveShadow>
        <planeGeometry args={GROUND_PLANE_ARGS} />
        <meshStandardMaterial color={MODEL.ground} roughness={1} />
      </mesh>
      <ShopSurfaces {...viewerProps} />
      {selected && <FindingAnnotation finding={selected} />}
      {route && <StopMarkers route={route} />}
    </Canvas>
  );
}
