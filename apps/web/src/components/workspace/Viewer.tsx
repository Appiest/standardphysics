"use client";

import { Canvas } from "@react-three/fiber";
import { Component, Suspense, type ReactNode } from "react";
import type { ViewerPose } from "@/lib/camera";
import type { Focus } from "@/lib/findings";
import type { NodeTextureCoverage, SceneGraph } from "@/types/contracts";
import { FindingAnnotation } from "./Annotation";
import { CameraRig } from "./CameraRig";
import { MODEL, outcomeColor } from "./palette";
import { type ArrangeHandlers, BoxShopModel, GlbShopModel } from "./ShopModel";
import { LidarShopModel } from "./LidarShopModel";
import { PaintedScan } from "./PaintedScan";
import { type RouteHandles, StopMarkers } from "./StopMarkers";

type ViewerProps = {
  scene: SceneGraph;
  exported: SceneGraph;
  arrange: ArrangeHandlers | null;
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
  staleNodeIds: string[];
  coverage: NodeTextureCoverage[];
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

function Lights() {
  return (
    <>
      <hemisphereLight args={["#ffffff", "#d8d2c4", 1.25]} />
      <directionalLight
        position={[3, 14, 5]}
        intensity={1.4}
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-12}
        shadow-camera-right={12}
        shadow-camera-top={12}
        shadow-camera-bottom={-12}
        shadow-bias={-0.0004}
      />
    </>
  );
}

type ShopSurfacesProps = Pick<ViewerProps, "scene" | "exported" | "arrange" | "glbUrl" | "scanGlbUrl" | "lidarUrl" | "selected" | "onSelectNode" | "cutWalls" | "materialMode" | "staleNodeIds" | "coverage">;

function modelPropsFor({ scene, arrange, selected, onSelectNode, cutWalls, materialMode, staleNodeIds, coverage }: ShopSurfacesProps) {
  return {
    shown: scene,
    focus: selected?.locus ? new Set(selected.locus.node_ids) : null,
    focusColor: selected ? outcomeColor(selected.outcome) : MODEL.accent,
    onSelectNode,
    arrange,
    cutWalls,
    // The boxes never render the scan: it replaces them. They appear in that
    // mode only as the fallback while the scan loads, and plain is right there.
    materialMode: materialMode === "scan" ? ("plain" as const) : materialMode,
    staleNodeIds: new Set(staleNodeIds),
    coverage: new Map(coverage.map((entry) => [entry.node_id, entry.textured_fraction])),
  };
}

function ShopSurfaces(props: ShopSurfacesProps) {
  const { exported, glbUrl, scanGlbUrl, lidarUrl, materialMode } = props;
  const modelProps = modelPropsFor(props);
  const boxes = <BoxShopModel {...modelProps} />;
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

export default function Viewer({ scene, exported, arrange, route, dragging, cutWalls, glbUrl, scanGlbUrl, lidarUrl, pose, selected, onSelectNode, onClearSelection, materialMode, staleNodeIds, coverage }: ViewerProps) {
  return (
    <Canvas
      frameloop="demand"
      dpr={[1, 2]}
      shadows
      camera={{ position: pose.position, fov: pose.fov, near: 0.05, far: 200 }}
      flat
      gl={{ antialias: true, localClippingEnabled: true }}
      onCreated={(state) => {
        if (process.env.NODE_ENV === "development") Object.assign(window, { __viewer: state });
      }}
      onPointerMissed={onClearSelection}
      aria-label="3D model of the shop"
    >
      <color attach="background" args={["#f6f5f1"]} />
      <Lights />
      <CameraRig pose={pose} locked={dragging} bounds={null} />
      <mesh rotation-x={-Math.PI / 2} position-y={-0.002} receiveShadow>
        <planeGeometry args={[80, 80]} />
        <meshStandardMaterial color={MODEL.ground} roughness={1} />
      </mesh>
      <ShopSurfaces scene={scene} exported={exported} arrange={arrange} glbUrl={glbUrl} scanGlbUrl={scanGlbUrl} lidarUrl={lidarUrl} selected={selected} onSelectNode={onSelectNode} cutWalls={cutWalls} materialMode={materialMode} staleNodeIds={staleNodeIds} coverage={coverage} />
      {selected && <FindingAnnotation finding={selected} />}
      {route && <StopMarkers route={route} />}
    </Canvas>
  );
}
