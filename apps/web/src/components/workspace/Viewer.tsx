"use client";

import { Canvas } from "@react-three/fiber";
import { Component, Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import type { ViewerPose } from "@/lib/camera";
import type { Focus } from "@/lib/findings";
import type { NodeTextureCoverage, SceneGraph, SceneNode } from "@/types/contracts";
import { FindingAnnotation } from "./Annotation";
import { CameraRig } from "./CameraRig";
import { MODEL, outcomeColor } from "./palette";
import { type ArrangeHandlers, BoxShopModel, GlbShopModel } from "./ShopModel";
import { LidarShopModel } from "./LidarShopModel";
import { PaintedScan } from "./PaintedScan";
import { GaussianSplatScan } from "./GaussianSplatScan";
import type { CapturedSplatAsset } from "@/lib/captured-splats";
import type { WheelchairProfile } from "@/lib/wheelchair-motion";
import { type RouteHandles, StopMarkers } from "./StopMarkers";
import { WheelchairController, type WheelchairState } from "./WheelchairController";

type ViewerProps = {
  scene: SceneGraph;
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
  materialMode: "reconstructed" | "captured" | "plain" | "coverage" | "scan";
  scanGlbUrl: string | null;
  splatAssets?: CapturedSplatAsset[];
  onSplatError?: (message: string | null) => void;
  staleNodeIds: string[];
  coverage: NodeTextureCoverage[];
  wheelchairMode?: boolean;
  wheelchairProfile?: WheelchairProfile;
  onWheelchairStateChange?: (state: WheelchairState) => void;
  wheelchairDockTarget?: SceneNode | null;
  onClearWheelchairDock?: () => void;
  onWheelchairSelectNode?: (node: SceneNode) => void;
  onWheelchairExit?: () => void;
};

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

function Lights() {
  return (
    <>
      <hemisphereLight args={HEMI_LIGHT_ARGS} />
      <directionalLight
        position={[6, 22, 10]}
        intensity={1.25}
        castShadow
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

type ShopSurfacesProps = Pick<ViewerProps, "scene" | "exported" | "arrange" | "dragAllNodes" | "lightweight" | "glbUrl" | "scanGlbUrl" | "splatAssets" | "onSplatError" | "lidarUrl" | "selected" | "onSelectNode" | "cutWalls" | "materialMode" | "staleNodeIds" | "coverage">;

function ShopSurfaces(props: ShopSurfacesProps) {
  const { exported, glbUrl, scanGlbUrl, lidarUrl, materialMode, selected, staleNodeIds, coverage, scene, arrange, dragAllNodes, lightweight, onSelectNode, cutWalls } = props;

  const focus = useMemo(() => (selected?.locus ? new Set(selected.locus.node_ids) : null), [selected]);
  const staleSet = useMemo(() => (staleNodeIds ? new Set(staleNodeIds) : EMPTY_STALE_SET), [staleNodeIds]);
  const coverageMap = useMemo(() => new Map(coverage.map((entry) => [entry.node_id, entry.textured_fraction])), [coverage]);

  const modelProps = useMemo(() => ({
    shown: scene,
    focus,
    focusColor: selected ? outcomeColor(selected.outcome) : MODEL.accent,
    onSelectNode,
    arrange,
    dragAllNodes,
    lightweight,
    cutWalls,
    materialMode: materialMode === "scan" ? ("plain" as const) : materialMode,
    staleNodeIds: staleSet,
    coverage: coverageMap,
  }), [scene, focus, selected, onSelectNode, arrange, dragAllNodes, lightweight, cutWalls, materialMode, staleSet, coverageMap]);

  const boxes = <BoxShopModel {...modelProps} />;
  const picking = <BoxShopModel {...modelProps} pickOnly />;
  if (materialMode === "scan" && props.splatAssets?.length) return <SplatRoom key={JSON.stringify(props.splatAssets)} assets={props.splatAssets} fallback={boxes} picking={picking} onError={props.onSplatError} />;
  if (materialMode === "scan" && scanGlbUrl) return <ScannedRoom url={scanGlbUrl} whileLoading={boxes} />;
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
function ScannedRoom({ url, whileLoading }: { url: string; whileLoading: ReactNode }) {
  return (
    <group>
      <GlbFallback key={url} fallback={whileLoading}>
        <Suspense fallback={whileLoading}><PaintedScan url={url} /></Suspense>
      </GlbFallback>
    </group>
  );
}

// eslint-disable-next-line complexity
export default function Viewer({
  scene,
  exported,
  arrange,
  dragAllNodes,
  lightweight,
  route,
  dragging,
  cutWalls,
  glbUrl,
  scanGlbUrl,
  splatAssets,
  onSplatError,
  lidarUrl,
  pose,
  selected,
  onSelectNode,
  onClearSelection,
  materialMode,
  staleNodeIds,
  coverage,
  wheelchairMode = false,
  wheelchairProfile,
  onWheelchairStateChange,
  wheelchairDockTarget = null,
  onClearWheelchairDock,
  onWheelchairSelectNode,
  onWheelchairExit,
}: ViewerProps) {
  return (
    <Canvas
      frameloop={wheelchairMode ? "always" : "demand"}
      dpr={lightweight ? DPR_LIGHTWEIGHT : splatAssets?.length ? DPR_SPLATS : DPR_DEFAULT}
      shadows={!lightweight}
      camera={{ position: pose.position, fov: pose.fov, near: 0.05, far: 200 }}
      flat
      gl={{ antialias: !splatAssets?.length, localClippingEnabled: true }}
      onCreated={(state) => {
        if (process.env.NODE_ENV === "development") Object.assign(window, { __viewer: state });
      }}
      onPointerMissed={onClearSelection}
      aria-label="3D model of the shop"
    >
      <color attach="background" args={BG_COLOR_ARGS} />
      <Lights />
      {!wheelchairMode && <CameraRig pose={pose} locked={dragging} bounds={null} />}
      {wheelchairMode && onWheelchairStateChange && wheelchairProfile && (
        <WheelchairController
          active={wheelchairMode}
          scene={scene}
          profile={wheelchairProfile}
          onStateChange={onWheelchairStateChange}
          dockTarget={wheelchairDockTarget}
          onClearDock={onClearWheelchairDock ?? (() => {})}
          onSelectNode={onWheelchairSelectNode ?? (() => {})}
          onExit={onWheelchairExit}
        />
      )}
      <mesh rotation-x={-Math.PI / 2} position-y={-0.002} receiveShadow>
        <planeGeometry args={GROUND_PLANE_ARGS} />
        <meshStandardMaterial color={MODEL.ground} roughness={1} />
      </mesh>
      <ShopSurfaces scene={scene} exported={exported} arrange={arrange} dragAllNodes={dragAllNodes} lightweight={lightweight} glbUrl={glbUrl} scanGlbUrl={scanGlbUrl} splatAssets={splatAssets} onSplatError={onSplatError} lidarUrl={lidarUrl} selected={selected} onSelectNode={onSelectNode} cutWalls={cutWalls} materialMode={materialMode} staleNodeIds={staleNodeIds} coverage={coverage} />
      {selected && <FindingAnnotation finding={selected} />}
      {route && <StopMarkers route={route} />}
    </Canvas>
  );
}
